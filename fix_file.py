import re

with open('source/03-protocol-builder/libs/fluentcoder/fluentcoder/catalog/indexer.py', 'r') as f:
    content = f.read()

content = content.replace('''                if (
                    cached
                    and cached["source_fingerprint"] == content_fp
                    and cached["entity_table"] == "liquid_classes"
                ):
                    if conn.execute(
                        "SELECT 1 FROM liquid_classes WHERE install_key = ? AND name = ?",
                        (install_key, cached["entity_key"]),
                    ).fetchone():
                        continue''', '''                if (
                    cached
                    and cached["source_fingerprint"] == content_fp
                    and cached["entity_table"] == "liquid_classes"
                ):
                    if cached["entity_key"] in existing_lqc_names:
                        continue''')

with open('source/03-protocol-builder/libs/fluentcoder/fluentcoder/catalog/indexer.py', 'w') as f:
    f.write(content)
