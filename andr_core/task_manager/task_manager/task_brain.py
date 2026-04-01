"""
task_brain.py — Priority-based task scheduler with preemption and state resume.

The TaskBrain sits above task_manager and controls what the agent works on.
It maintains a priority queue and can preempt lower-priority running tasks
to service higher-priority ones.

Priority scale (1–10, higher = more important):
    1      — lowest (wander / idle behavior)
    2      — scheduled / cron tasks
    5      — default (user-initiated tasks from UI, input sources, CLI)
    8–10   — urgent / interrupt-level tasks (safety, critical alerts)

When a higher-priority task arrives while a lower-priority task is running:
    1. Cancel the running task
    2. Save the agent's latest feedback as state
    3. Push the preempted task onto a resume stack
    4. Execute the new task
    5. After completion, resume the preempted task with saved context

If the new task has priority <= the current task, it is queued and will
execute after the current task completes.

Wander mode (optional):
    When enabled and no other tasks are pending, the brain sends idle prompts
    to keep the agent active — exploring, observing, practicing skills, etc.
"""

from __future__ import annotations

import heapq
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import rclpy
from rclpy.action import ActionClient, ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node

from andr_msgs.action import TaskGoal

logger = logging.getLogger(__name__)


# ── Priority constants (1–10 scale, higher = more important) ─────────────────

DEFAULT_PRIORITY = 5       # Default for user-initiated tasks
IDLE_PRIORITY = 1          # Wander / idle behavior
SCHEDULED_PRIORITY = 2     # Cron-like recurring tasks


# ── Task representation ─────────────────────────────────────────────────────

@dataclass(order=True)
class Task:
    """A task in the priority queue. Lower sort key = processed first."""
    sort_key: tuple = field(compare=True, repr=False)
    id: str = field(compare=False, default_factory=lambda: uuid.uuid4().hex[:8])
    prompt: str = field(compare=False, default="")
    context: str = field(compare=False, default="")
    priority: int = field(compare=False, default=DEFAULT_PRIORITY)
    saved_state: str = field(compare=False, default="")
    is_resume: bool = field(compare=False, default=False)

    @staticmethod
    def create(prompt: str, context: str = "", priority: int = DEFAULT_PRIORITY,
               saved_state: str = "", is_resume: bool = False) -> Task:
        # Negate priority so higher priority = lower sort key (popped first)
        # Use time as tiebreaker (earlier = first)
        sort_key = (-priority, time.monotonic())
        return Task(
            sort_key=sort_key, prompt=prompt, context=context,
            priority=priority, saved_state=saved_state, is_resume=is_resume,
        )


# ── Scheduled task definition ────────────────────────────────────────────────

@dataclass
class ScheduledTask:
    """A recurring task on a timer."""
    name: str
    prompt: str
    context: str
    interval_sec: float
    priority: int = SCHEDULED_PRIORITY
    enabled: bool = True
    last_run: float = 0.0


# ── Wander prompts ──────────────────────────────────────────────────────────

WANDER_PROMPTS = [
    "You're idle. Look around and describe what you observe.",
    "You have free time. Do something useful — explore or practice a skill.",
    "Nothing is happening. Check your surroundings and report anything interesting.",
    "You're in standby. Pick something constructive to do on your own.",
    "Idle time. Wander around and see if anything needs attention.",
]


class TaskBrain(Node):
    """Priority-based task scheduler with preemption and state save/resume.

    Replaces the C++ BehaviorTree brain with a Python node that:
    - Accepts tasks via /task_brain/submit (action server)
    - Forwards them to /task_manager/execute with priority ordering
    - Preempts lower-priority tasks when higher-priority ones arrive
    - Saves agent state on preemption, resumes afterward
    - Optionally runs wander behavior when idle
    - Supports scheduled/cron tasks
    """

    SUBMIT_ACTION = "/task_brain/submit"
    TASK_MANAGER_ACTION = "/task_manager/execute"

    def __init__(self):
        super().__init__("task_brain")

        # ── Parameters ────────────────────────────────────────────────────
        self.declare_parameter("enable_wander", False)
        self.declare_parameter("wander_interval_sec", 60.0)
        self.declare_parameter("wander_prompt", "")
        self.declare_parameter("enable_scheduler", True)
        self.declare_parameter("resume_preempted", True)

        self._enable_wander = self.get_parameter("enable_wander").value
        self._wander_interval = self.get_parameter("wander_interval_sec").value
        self._custom_wander = self.get_parameter("wander_prompt").value
        self._enable_scheduler = self.get_parameter("enable_scheduler").value
        self._resume_preempted = self.get_parameter("resume_preempted").value

        # ── State ─────────────────────────────────────────────────────────
        self._queue: list[Task] = []  # min-heap
        self._lock = threading.Lock()
        self._current_task: Optional[Task] = None
        self._current_goal_handle = None  # goal handle for the running task_manager call
        self._running = False
        self._preempted_stack: list[Task] = []  # tasks that were preempted (LIFO for resume)
        self._wander_index = 0
        self._scheduled_tasks: dict[str, ScheduledTask] = {}

        # ── Callbacks ─────────────────────────────────────────────────────
        self._cb_group = ReentrantCallbackGroup()

        # Action client to task_manager
        self._task_client = ActionClient(
            self, TaskGoal, self.TASK_MANAGER_ACTION,
            callback_group=self._cb_group,
        )

        # Action server for external task submission
        self._action_server = ActionServer(
            self, TaskGoal, self.SUBMIT_ACTION,
            execute_callback=self._submit_execute_cb,
            goal_callback=self._submit_goal_cb,
            cancel_callback=self._submit_cancel_cb,
            callback_group=self._cb_group,
        )

        # ── Scheduler timer ───────────────────────────────────────────────
        self._scheduler_timer = self.create_timer(
            5.0, self._scheduler_tick, callback_group=self._cb_group,
        )

        # ── Main loop timer ───────────────────────────────────────────────
        self._loop_timer = self.create_timer(
            0.5, self._loop_tick, callback_group=self._cb_group,
        )

        self.get_logger().info(
            f"TaskBrain ready — wander={'on' if self._enable_wander else 'off'}, "
            f"scheduler={'on' if self._enable_scheduler else 'off'}, "
            f"resume_preempted={self._resume_preempted}"
        )

    # ══════════════════════════════════════════════════════════════════════
    # Public API — submit tasks
    # ══════════════════════════════════════════════════════════════════════

    def submit_task(self, prompt: str, context: str = "",
                    priority: int = DEFAULT_PRIORITY) -> str:
        """Add a task to the queue. Returns the task ID."""
        priority = max(1, min(10, priority))  # clamp to 1–10
        task = Task.create(prompt, context, priority)
        with self._lock:
            heapq.heappush(self._queue, task)
        self.get_logger().info(
            f"Task queued: [priority={priority}] id={task.id} "
            f"prompt='{prompt[:60]}'"
        )
        return task.id

    def add_scheduled_task(self, name: str, prompt: str, interval_sec: float,
                           context: str = "", priority: int = SCHEDULED_PRIORITY) -> None:
        """Register a recurring scheduled task."""
        self._scheduled_tasks[name] = ScheduledTask(
            name=name, prompt=prompt, context=context,
            interval_sec=interval_sec, priority=priority,
        )
        self.get_logger().info(
            f"Scheduled task registered: '{name}' every {interval_sec}s"
        )

    def remove_scheduled_task(self, name: str) -> bool:
        """Remove a scheduled task by name."""
        if name in self._scheduled_tasks:
            del self._scheduled_tasks[name]
            self.get_logger().info(f"Scheduled task removed: '{name}'")
            return True
        return False

    # ══════════════════════════════════════════════════════════════════════
    # Action server callbacks (external task submission)
    # ══════════════════════════════════════════════════════════════════════

    def _submit_goal_cb(self, goal_request) -> GoalResponse:
        if not goal_request.prompt.strip():
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def _submit_cancel_cb(self, goal_handle) -> CancelResponse:
        return CancelResponse.ACCEPT

    def _submit_execute_cb(self, goal_handle) -> TaskGoal.Result:
        """Handle an externally submitted task.

        Reads priority from the goal's priority field (1–10, default 5).
        Queues the task and waits for it to complete before returning.
        """
        goal = goal_handle.request
        priority = goal.priority if goal.priority > 0 else DEFAULT_PRIORITY

        task = Task.create(goal.prompt, goal.context, priority)
        done_event = threading.Event()
        task_result = {"success": False, "summary": ""}

        # Store a completion callback on the task
        task._done_event = done_event
        task._result = task_result

        with self._lock:
            heapq.heappush(self._queue, task)

        self.get_logger().info(
            f"External task queued: [priority={priority}] "
            f"id={task.id} prompt='{goal.prompt[:60]}'"
        )

        # Relay: publish feedback while waiting
        self._send_submit_feedback(goal_handle, "queued", "Task queued", 0.0)

        # Wait for completion (the main loop will process it)
        done_event.wait()

        result = TaskGoal.Result()
        result.success = task_result["success"]
        result.summary = task_result["summary"]

        if result.success:
            goal_handle.succeed()
        else:
            goal_handle.abort()

        return result

    # ══════════════════════════════════════════════════════════════════════
    # Main loop — processes queue, handles preemption
    # ══════════════════════════════════════════════════════════════════════

    def _loop_tick(self) -> None:
        """Called periodically. Checks queue and dispatches tasks."""
        with self._lock:
            if self._running:
                # Check if a higher-priority task should preempt
                if self._queue and self._current_task:
                    next_task = self._queue[0]
                    next_prio = next_task.priority
                    curr_prio = self._current_task.priority

                    if next_prio > curr_prio:
                        self.get_logger().info(
                            f"Preempting [priority={curr_prio}] task "
                            f"'{self._current_task.prompt[:40]}' for "
                            f"[priority={next_prio}] task "
                            f"'{next_task.prompt[:40]}'"
                        )
                        self._preempt_current()
                return

            # Not running — pick next task from queue
            if self._queue:
                task = heapq.heappop(self._queue)
            elif self._preempted_stack and self._resume_preempted:
                # Resume the most recently preempted task
                task = self._preempted_stack.pop()
                self.get_logger().info(
                    f"Resuming preempted task: id={task.id} "
                    f"prompt='{task.prompt[:60]}'"
                )
            elif self._enable_wander:
                task = self._create_wander_task()
            else:
                return

        # Dispatch outside the lock
        self._dispatch_task(task)

    def _preempt_current(self) -> None:
        """Cancel the running task and save its state for later resume."""
        if not self._current_task or not self._current_goal_handle:
            return

        task = self._current_task
        self.get_logger().info(f"Cancelling task id={task.id} for preemption...")

        # Request cancel on the task_manager goal
        cancel_future = self._current_goal_handle.cancel_goal_async()
        # Don't wait — the result callback will handle cleanup

        # Save state: the task gets queued for resume with whatever context
        # the agent already produced (feedback state)
        if self._resume_preempted and task.priority > IDLE_PRIORITY:
            task.saved_state = (
                f"This task was interrupted before completion. "
                f"Original prompt: {task.prompt}"
            )
            task.is_resume = True
            self._preempted_stack.append(task)
            self.get_logger().info(
                f"Saved preempted task for resume: id={task.id}"
            )

        self._running = False
        self._current_task = None
        self._current_goal_handle = None

    def _dispatch_task(self, task: Task) -> None:
        """Send a task to task_manager and track it."""
        if not self._task_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error("task_manager not available — requeueing task")
            with self._lock:
                heapq.heappush(self._queue, task)
            return

        goal = TaskGoal.Goal()

        # Build prompt with resume context if applicable
        if task.is_resume and task.saved_state:
            goal.prompt = (
                f"[RESUMING INTERRUPTED TASK]\n"
                f"{task.saved_state}\n\n"
                f"Continue or restart this task as you see fit."
            )
        else:
            goal.prompt = task.prompt

        goal.context = task.context
        goal.priority = task.priority

        self._running = True
        self._current_task = task

        self.get_logger().info(
            f"Dispatching [priority={task.priority}] task id={task.id}: "
            f"'{task.prompt[:60]}'"
        )

        future = self._task_client.send_goal_async(
            goal,
            feedback_callback=lambda fb: self._on_task_feedback(task, fb),
        )
        future.add_done_callback(lambda f: self._on_goal_accepted(task, f))

    def _on_goal_accepted(self, task: Task, future) -> None:
        """Called when task_manager accepts or rejects the goal."""
        try:
            goal_handle = future.result()
            if goal_handle is None or not goal_handle.accepted:
                self.get_logger().warn(
                    f"task_manager rejected task id={task.id}"
                )
                self._running = False
                self._current_task = None
                self._notify_task_done(task, False, "Rejected by task_manager")
                return

            self._current_goal_handle = goal_handle
            result_future = goal_handle.get_result_async()
            result_future.add_done_callback(
                lambda f: self._on_task_result(task, f)
            )
        except Exception as exc:
            self.get_logger().error(f"Goal accept error: {exc}")
            self._running = False
            self._current_task = None
            self._notify_task_done(task, False, str(exc))

    def _on_task_feedback(self, task: Task, feedback_msg) -> None:
        """Store latest feedback for state saving."""
        fb = feedback_msg.feedback
        # Store the latest status so we can save it if preempted
        task.saved_state = (
            f"Task was in state '{fb.state}'. "
            f"Last status: {fb.status}. "
            f"Progress: {fb.progress:.0%}. "
            f"Original prompt: {task.prompt}"
        )

    def _on_task_result(self, task: Task, future) -> None:
        """Called when the dispatched task completes."""
        self._running = False
        self._current_task = None
        self._current_goal_handle = None

        try:
            wrapped = future.result()
            if wrapped is None:
                self.get_logger().warn(f"Task id={task.id} returned no result")
                self._notify_task_done(task, False, "No result")
                return

            result = wrapped.result
            self.get_logger().info(
                f"Task id={task.id} "
                f"{'succeeded' if result.success else 'failed'}: "
                f"{result.summary[:100]}"
            )
            self._notify_task_done(task, result.success, result.summary)
        except Exception as exc:
            self.get_logger().error(f"Task result error: {exc}")
            self._notify_task_done(task, False, str(exc))

    def _notify_task_done(self, task: Task, success: bool, summary: str) -> None:
        """Signal completion to external callers waiting on this task."""
        if hasattr(task, "_done_event"):
            task._result["success"] = success
            task._result["summary"] = summary
            task._done_event.set()

    # ══════════════════════════════════════════════════════════════════════
    # Scheduler — fires scheduled tasks on their intervals
    # ══════════════════════════════════════════════════════════════════════

    def _scheduler_tick(self) -> None:
        """Check all scheduled tasks and fire any that are due."""
        if not self._enable_scheduler:
            return

        now = time.monotonic()
        for st in self._scheduled_tasks.values():
            if not st.enabled:
                continue
            if now - st.last_run >= st.interval_sec:
                st.last_run = now
                self.submit_task(st.prompt, st.context, st.priority)
                self.get_logger().info(
                    f"Scheduled task fired: '{st.name}'"
                )

    # ══════════════════════════════════════════════════════════════════════
    # Wander — optional idle behavior
    # ══════════════════════════════════════════════════════════════════════

    _last_wander = 0.0

    def _create_wander_task(self) -> Optional[Task]:
        """Create a low-priority wander task if enough time has passed."""
        now = time.monotonic()
        if now - self._last_wander < self._wander_interval:
            return None

        self._last_wander = now

        if self._custom_wander:
            prompt = self._custom_wander
        else:
            prompt = WANDER_PROMPTS[self._wander_index % len(WANDER_PROMPTS)]
            self._wander_index += 1

        self.get_logger().info(f"Wander: '{prompt[:60]}'")
        return Task.create(prompt, context="wander", priority=IDLE_PRIORITY)

    # ══════════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════════

    def _send_submit_feedback(self, goal_handle, state, status, progress) -> None:
        fb = TaskGoal.Feedback()
        fb.state = state
        fb.status = status
        fb.progress = float(max(0.0, min(1.0, progress)))
        goal_handle.publish_feedback(fb)

    def destroy(self):
        self._action_server.destroy()
        self._task_client.destroy()
        super().destroy_node()


def main(args=None):
    logging.basicConfig(level=logging.INFO)
    rclpy.init(args=args)
    brain = TaskBrain()
    executor = MultiThreadedExecutor()
    executor.add_node(brain)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        brain.destroy()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
