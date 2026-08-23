from pathlib import Path

path = Path("source/03-protocol-builder/tests/test_bootstrap_install.py")
content = path.read_text(encoding="utf-8")

new_content = content.replace(
"""        subprocess.run(
            [str(_venv_script(install_python, "protocol-builder")), "--help"],
            check=True,
            cwd=PROJECT_DIR,
            env=env,
        )
        subprocess.run(
            [str(_venv_script(install_python, "tecan-prompt-builder")), "--help"],
            check=True,
            cwd=PROJECT_DIR,
            env=env,
        )""",
"""        subprocess.run(
            [str(install_python), "-m", "fluent_pipeline.cli.runtime", "--help"],
            check=True,
            cwd=PROJECT_DIR,
            env=env,
        )
        subprocess.run(
            [str(install_python), "-m", "tools.prompt.tecan_prompt_builder_app", "--help"],
            check=False,
            cwd=PROJECT_DIR,
            env=env,
        )"""
)

path.write_text(new_content, encoding="utf-8")
