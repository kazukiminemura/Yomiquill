from local_ai import CHAT_MODEL, EMBED_MODEL, data_home
from local_vision import VISION_MODEL


MODEL_FILES = (
    (CHAT_MODEL, "chat", ("openvino_model",)),
    (EMBED_MODEL, "embedding", ("openvino_model",)),
    (VISION_MODEL, "vision", (
        "openvino_language_model", "openvino_text_embeddings_model",
        "openvino_vision_embeddings_model", "openvino_vision_embeddings_merger_model",
        "openvino_vision_embeddings_pos_model",
    )),
)


def model_ready(directory, stems):
    names = ["config.json", "tokenizer.json", "tokenizer_config.json", "chat_template.jinja"]
    for stem in (*stems, "openvino_tokenizer", "openvino_detokenizer"):
        names.extend((f"{stem}.bin", f"{stem}.xml"))
    if directory.name == "vision":
        names.extend(("preprocessor_config.json", "processor_config.json"))
    return all((directory / name).is_file() and (directory / name).stat().st_size > 0 for name in names)


def prepare_models():
    models = data_home() / "models"
    for repository, folder, stems in MODEL_FILES:
        target = models / folder
        if model_ready(target, stems):
            continue
        from huggingface_hub import snapshot_download

        print(f"[2/3] モデルを準備しています: {folder}", flush=True)
        snapshot_download(
            repo_id=repository,
            local_dir=target,
            allow_patterns=["*.xml", "*.bin", "*.json", "*.jinja", "*.txt", "LICENSE*"],
        )
        if not model_ready(target, stems):
            raise SystemExit(f"{folder} モデルのファイルが不足しています。もう一度お試しください。")


if __name__ == "__main__":
    prepare_models()
