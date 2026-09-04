import os
import re

def main():
    root_dir = "source/03-protocol-builder/libs/fluentcoder"

    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            if "SimulationOptions" in content and "from fluentcoder.simulator.options import SimulationOptions" not in content:
                # Need to inject import correctly
                if filename != "worktable.py":
                    import_line = "from fluentcoder.simulator.options import SimulationOptions\n"
                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        if not line.strip().startswith('"""') and not line.strip().startswith('#'):
                            if line.strip() == "":
                                continue
                            # Put it right here
                            lines.insert(i, import_line)
                            break
                    with open(path, "w", encoding="utf-8") as f:
                        f.write('\n'.join(lines))

if __name__ == "__main__":
    main()
