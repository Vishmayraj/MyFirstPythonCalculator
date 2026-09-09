"""
Model 2 — Fine-tune YOLO26m on vehicles-q0x2v dataset
Downloads dataset from HuggingFace, converts to YOLO format, trains the model.
"""

import logging
import shutil
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import (
    HF_DATASET_NAME,
    DATASET_DIR,
    DATASET_YAML,
    WEIGHTS_DIR,
    YOLO26M_PRETRAINED,
    EPOCHS,
    BATCH_SIZE,
    IMG_SIZE,
    LEARNING_RATE,
    PATIENCE,
    VEHICLE_CLASSES,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("sentinel.training")


def download_dataset():
    """Download vehicles-q0x2v from HuggingFace and prepare YOLO format."""
    from datasets import load_dataset

    logger.info(f"Downloading {HF_DATASET_NAME} from HuggingFace...")
    dataset = load_dataset(HF_DATASET_NAME)

    # Create YOLO directory structure
    for split in ["train", "validation", "test"]:
        (DATASET_DIR / split / "images").mkdir(parents=True, exist_ok=True)
        (DATASET_DIR / split / "labels").mkdir(parents=True, exist_ok=True)

    logger.info("Dataset downloaded. Converting COCO annotations to YOLO format...")
    _convert_to_yolo(dataset)

    # Create dataset.yaml
    yaml_content = f"""# vehicles-q0x2v YOLO format dataset
path: {DATASET_DIR.absolute()}
train: train/images
val: validation/images
test: test/images

names:
"""
    for cls_id, cls_name in sorted(VEHICLE_CLASSES.items()):
        yaml_content += f"  {cls_id}: {cls_name}\n"

    DATASET_YAML.write_text(yaml_content)
    logger.info(f"Dataset YAML written to {DATASET_YAML}")
    return dataset


def _convert_to_yolo(dataset):
    """Convert COCO-format annotations to YOLO normalized format."""
    split_map = {"train": "train", "validation": "validation", "test": "test"}

    for split_name, yolo_split in split_map.items():
        if split_name not in dataset:
            continue
        split_data = dataset[split_name]
        logger.info(f"Converting {split_name}: {len(split_data)} images...")

        for idx, sample in enumerate(split_data):
            img = sample["image"]
            width = sample["width"]
            height = sample["height"]
            objects = sample["objects"]

            # Save image
            img_filename = f"{split_name}_{idx:06d}.jpg"
            img_path = DATASET_DIR / yolo_split / "images" / img_filename
            img.save(str(img_path))

            # Convert annotations to YOLO format
            label_filename = f"{split_name}_{idx:06d}.txt"
            label_path = DATASET_DIR / yolo_split / "labels" / label_filename

            lines = []
            for i in range(len(objects["id"])):
                bbox = objects["bbox"][i]  # COCO format: [x, y, width, height]
                category = objects["category"][i]

                # Dataset uses class IDs 1-12, YOLO expects 0-11
                yolo_class = category - 1
                if yolo_class < 0:
                    continue  # skip class 0 (unused)

                # Convert to YOLO normalized: center_x, center_y, width, height
                x_center = (bbox[0] + bbox[2] / 2) / width
                y_center = (bbox[1] + bbox[3] / 2) / height
                norm_w = bbox[2] / width
                norm_h = bbox[3] / height

                # Clamp values to [0, 1]
                x_center = max(0, min(1, x_center))
                y_center = max(0, min(1, y_center))
                norm_w = max(0, min(1, norm_w))
                norm_h = max(0, min(1, norm_h))

                lines.append(f"{yolo_class} {x_center:.6f} {y_center:.6f} {norm_w:.6f} {norm_h:.6f}")

            label_path.write_text("\n".join(lines))

        logger.info(f"  {split_name}: {len(split_data)} images converted.")


def train_model():
    """Fine-tune YOLO26m on the vehicles dataset."""
    from ultralytics import YOLO
    import torch

    # Fix cuDNN stream mismatch error and memory issues
    torch.backends.cudnn.enabled = False
    torch.backends.cudnn.benchmark = False

    # Load pretrained YOLO26m
    model = YOLO(YOLO26M_PRETRAINED)
    logger.info(f"Loaded pretrained model: {YOLO26M_PRETRAINED}")

    # Train with CPU and smaller batch size to avoid memory issues
    results = model.train(
        data=str(DATASET_YAML),
        epochs=EPOCHS,
        batch=BATCH_SIZE,
        imgsz=IMG_SIZE,
        lr0=LEARNING_RATE,
        patience=PATIENCE,
        device="cpu",
        project=str(WEIGHTS_DIR),
        name="yolo26m_vehicles",
        exist_ok=True,
        pretrained=True,
        optimizer="AdamW",
        cos_lr=True,
        augment=True,
        mosaic=1.0,
        mixup=0.1,
        copy_paste=0.1,
        degrees=5.0,
        translate=0.1,
        scale=0.5,
        fliplr=0.5,
        flipud=0.0,
        verbose=True,
    )

    # Copy best weights to standard location
    run_dir = WEIGHTS_DIR / "yolo26m_vehicles"
    best_weights = run_dir / "weights" / "best.pt"
    if best_weights.exists():
        shutil.copy2(best_weights, WEIGHTS_DIR / "yolo26m_vehicles_best.pt")
        logger.info(f"Best weights saved to: {WEIGHTS_DIR / 'yolo26m_vehicles_best.pt'}")

    logger.info("Training complete!")
    return results


def main():
    """Download dataset, convert, train."""
    logger.info("=" * 60)
    logger.info("Model 2 — YOLO26m Fine-Tuning Pipeline")
    logger.info("=" * 60)

    # Step 1: Download and prepare dataset
    download_dataset()

    # Step 2: Train
    results = train_model()

    logger.info("=" * 60)
    logger.info("Pipeline complete! Next step: run evaluate.py")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
