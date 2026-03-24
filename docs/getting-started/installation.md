# Installation

## Option A: pip (recommended)

Requires ROS 2 Humble on the host.

```bash
sudo apt install ros-humble-ros-base
source /opt/ros/humble/setup.bash
pip install andr
```

Verify:

```bash
andr --help
```

## Option B: Docker

No ROS 2 installation required.

```bash
git clone https://github.com/jaximus808/andr.git
cd andr
docker compose up
```

Pull a model (first time only):

```bash
docker exec -it andr-ollama ollama pull llama3.2
```

Open [http://localhost:8080](http://localhost:8080).

### Docker environment variables

| Variable | Default | Description |
|---|---|---|
| `ANDR_LLM_BACKEND` | `ollama` | `ollama` or `openai` |
| `ANDR_LLM_MODEL` | `llama3.2` | Model name |
| `ANDR_LLM_TEMPERATURE` | `0.2` | Sampling temperature |
| `ANDR_UI_PORT` | `8080` | Web UI port |
| `ANDR_TOOLS` | | Comma-separated tools to launch |
| `OPENAI_API_KEY` | | Required for `openai` backend |

GPU support (Ollama): uncomment the `deploy` section in `docker-compose.yml`.

## Option C: Build from source

For contributors or full colcon workspace control. See [Building from Source](../from-source.md).
