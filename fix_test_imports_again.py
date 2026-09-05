import os

def main():
    root_dir = "source/03-protocol-builder/libs/fluentcoder"

    for dirpath, _, filenames in os.walk(root_dir):
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(dirpath, filename)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            orig_content = content

            # Remove bad imports
            content = content.replace("from fluentcoder.simulator.options import SimulationOptions\nfrom __future__ import annotations\n", "from __future__ import annotations\n")

            if "SimulationOptions" in content and filename != "options.py" and filename != "worktable.py":
                # Make sure we don't duplicate
                if "from fluentcoder.simulator.options import SimulationOptions" in content:
                    content = content.replace("from fluentcoder.simulator.options import SimulationOptions\n", "")

                import_line = "from fluentcoder.simulator.options import SimulationOptions\n"

                lines = content.split('\n')
                insert_idx = 0
                for i, line in enumerate(lines):
                    if "from __future__" in line:
                        insert_idx = i + 1
                        break
                    elif line.startswith("import ") or line.startswith("from "):
                        insert_idx = i
                        break

                lines.insert(insert_idx, import_line)
                content = '\n'.join(lines)

            if content != orig_content:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)

if __name__ == "__main__":
    main()
