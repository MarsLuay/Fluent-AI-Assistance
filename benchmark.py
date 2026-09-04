import time
import os
from pathlib import Path
from fluentcoder.catalog.indexer import build_index

def create_fake_install(install_dir, n=5000):
    install_dir.mkdir(parents=True, exist_ok=True)
    sites_dir = install_dir / "SystemSpecific" / "Worktable" / "Sites"
    sites_dir.mkdir(parents=True, exist_ok=True)
    components_dir = install_dir / "SystemSpecific" / "Worktable" / "Components"
    components_dir.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        with open(sites_dir / f"site_{i}.xsit", "w") as f:
            f.write(f"fake site data {i}")

def main():
    install_dir = Path("/tmp/fake_install")
    create_fake_install(install_dir, 5000)

    db_path = Path("/tmp/fake_index.db")
    if db_path.exists():
        db_path.unlink()

    print("Building index first time (cold)...")
    start = time.time()
    build_index(install_path=install_dir, db_path=db_path)
    cold_time = time.time() - start
    print(f"Cold time: {cold_time:.4f}s")

    print("Building index second time (warm)...")
    start = time.time()
    build_index(install_path=install_dir, db_path=db_path)
    warm_time = time.time() - start
    print(f"Warm time: {warm_time:.4f}s")

if __name__ == "__main__":
    main()
