from huggingface_hub import snapshot_download

from local_ai import CHAT_MODEL, EMBED_MODEL, data_home
from local_vision import VISION_MODEL


if __name__ == "__main__":
    for repository, folder in ((CHAT_MODEL, "chat"), (EMBED_MODEL, "embedding"), (VISION_MODEL, "vision")):
        print(f"モデルを準備中: {repository}", flush=True)
        snapshot_download(
            repo_id=repository,
            local_dir=data_home() / "models" / folder,
            allow_patterns=["*.xml", "*.bin", "*.json", "*.jinja", "*.txt", "LICENSE*"],
        )
