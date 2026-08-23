"""
Database layer for Tecan protocol knowledge base.

Provides persistent storage for:
- Command definitions and templates
- Observed parameter values
- Command sequences and patterns (future)
- Protocol logic extraction (future)

Currently uses SQLite for simplicity (no server required).
Can migrate to PostgreSQL for multi-user scenarios.
"""

import sqlite3
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from contextlib import contextmanager


# Database path — bundled inside the catalog package.
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "tecan.db"


class TecanDatabase:
    """
    SQLite-based knowledge base for Tecan commands and patterns.

    Designed to eventually support:
    - Command syntax (current)
    - Sequence patterns (what follows what)
    - Protocol logic extraction
    - Parameter correlations
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._migrate_schema()
        self.seed_dsl_recipes()

    def _validate_identifier(self, name: str) -> str:
        """Validate SQL identifier to prevent SQL injection."""
        if not re.match(r'^[a-zA-Z0-9_]+$', name):
            raise ValueError(f"Invalid SQL identifier: {name}")
        return name

    @contextmanager
    def _connection(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        """Initialize database schema."""
        with self._connection() as conn:
            conn.executescript("""
                -- Core command definitions
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY,
                    type TEXT UNIQUE NOT NULL,
                    category TEXT,
                    description TEXT,
                    template TEXT,
                    confidence REAL DEFAULT 1.0,
                    created_at TEXT,
                    updated_at TEXT,
                    source_files TEXT  -- JSON array
                );

                -- Command parameters
                CREATE TABLE IF NOT EXISTS parameters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command_id TEXT NOT NULL REFERENCES commands(id),
                    name TEXT NOT NULL,
                    type TEXT,
                    required INTEGER DEFAULT 0,
                    default_value TEXT,
                    description TEXT,
                    UNIQUE(command_id, name)
                );

                -- Observed parameter values
                CREATE TABLE IF NOT EXISTS observed_values (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    parameter_id INTEGER NOT NULL REFERENCES parameters(id),
                    value TEXT NOT NULL,
                    frequency INTEGER DEFAULT 1,
                    first_seen TEXT,
                    source_file TEXT,
                    UNIQUE(parameter_id, value)
                );

                -- Global observed values (labware, locations, etc.)
                CREATE TABLE IF NOT EXISTS global_values (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL,  -- 'labware_type', 'location', 'liquid_class'
                    value TEXT NOT NULL,
                    frequency INTEGER DEFAULT 1,
                    first_seen TEXT,
                    UNIQUE(category, value)
                );

                -- Labware definitions (full details)
                CREATE TABLE IF NOT EXISTS labware (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    category TEXT NOT NULL,  -- 'plate', 'tip_box', 'reservoir', 'tube_rack', 'unknown'
                    functional_group TEXT,
                    wells INTEGER,
                    rows INTEGER,
                    columns INTEGER,
                    x_spacing REAL,
                    y_spacing REAL,
                    properties TEXT,  -- JSON for additional properties
                    source_file TEXT,
                    created_at TEXT,
                    updated_at TEXT
                );

                -- Liquid class definitions
                CREATE TABLE IF NOT EXISTS liquid_classes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    device_type TEXT NOT NULL,  -- 'Fca', 'AirFca', 'Mca384', 'Mca96'
                    description TEXT,
                    aspiration_speed TEXT,  -- Can be formula or number
                    dispense_speed TEXT,
                    key_parameters TEXT,  -- JSON
                    all_parameters TEXT,  -- JSON
                    conditions TEXT,  -- JSON array
                    source_file TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(name, device_type)
                );

                -- Command sequences (what follows what)
                CREATE TABLE IF NOT EXISTS sequences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_command_id TEXT REFERENCES commands(id),
                    to_command_id TEXT REFERENCES commands(id),
                    frequency INTEGER DEFAULT 1,
                    contexts TEXT,  -- JSON: what conditions trigger this sequence
                    UNIQUE(from_command_id, to_command_id)
                );

                -- Named patterns (reusable workflows)
                CREATE TABLE IF NOT EXISTS patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    pattern_type TEXT,  -- 'sequence', 'loop', 'conditional'
                    steps TEXT NOT NULL,  -- JSON array of command IDs
                    parameters TEXT,  -- JSON object with default parameters
                    frequency INTEGER DEFAULT 1,
                    confidence REAL DEFAULT 0.5,
                    created_at TEXT,
                    updated_at TEXT
                );

                -- Discovered rules (compatibility, constraints, best practices)
                CREATE TABLE IF NOT EXISTS rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    rule_type TEXT NOT NULL,  -- 'compatibility', 'constraint', 'best_practice', 'workflow'
                    category TEXT,  -- 'labware', 'liquid_class', 'tips', 'general'
                    protocol_type TEXT,  -- NULL = applies to all protocol types
                    description TEXT NOT NULL,
                    scope TEXT DEFAULT 'global',  -- 'global', 'domain', 'module'
                    severity TEXT DEFAULT 'soft',  -- 'hard', 'soft'
                    conditions TEXT,  -- JSON: when the rule applies
                    requirements TEXT,  -- JSON: what the rule requires
                    examples TEXT,  -- JSON array of examples
                    source TEXT NOT NULL,  -- 'extraction', 'conversation', 'manual'
                    source_context TEXT,  -- Additional context about where rule came from
                    confidence REAL DEFAULT 0.5,
                    active INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                );

                -- Semantic workflow modules
                CREATE TABLE IF NOT EXISTS modules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    domain TEXT DEFAULT 'general',
                    description TEXT NOT NULL,
                    preconditions TEXT,  -- JSON
                    step_template TEXT,  -- JSON
                    constraints TEXT,  -- JSON
                    confidence REAL DEFAULT 0.5,
                    active INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                );

                -- Curated executable wrong/right DSL/API recipes for LM repair
                CREATE TABLE IF NOT EXISTS dsl_recipes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    object_key TEXT NOT NULL,
                    action TEXT NOT NULL,
                    failure_category TEXT,
                    bad_pattern TEXT,
                    good_patterns TEXT NOT NULL,
                    context_text TEXT,
                    tags TEXT,
                    embedding TEXT,
                    active INTEGER DEFAULT 1,
                    created_at TEXT,
                    updated_at TEXT
                );

                -- Evidence records linking validation findings to learned rules
                CREATE TABLE IF NOT EXISTS rule_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    rule_name TEXT NOT NULL,
                    protocol_name TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    line_number INTEGER,
                    severity TEXT DEFAULT 'soft',
                    source TEXT DEFAULT 'infopad',
                    created_at TEXT,
                    UNIQUE(run_id, rule_name, error_code, line_number)
                );

                -- Worktable positions (valid positions for each location)
                CREATE TABLE IF NOT EXISTS worktable_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    location TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    frequency INTEGER DEFAULT 1,
                    example_labware TEXT,  -- Example of what goes here
                    notes TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    UNIQUE(location, position)
                );

                -- Head adapters configuration
                CREATE TABLE IF NOT EXISTS adapters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,  -- e.g., 'EVA', '384_Combo'
                    display_name TEXT NOT NULL,  -- e.g., 'EVA (Extended Volume)'
                    labware_pattern TEXT NOT NULL,  -- Pattern to match labware names, e.g., 'EVA*'
                    x_count INTEGER NOT NULL,  -- Max columns (12 for EVA, 24 for 384)
                    y_count INTEGER NOT NULL,  -- Max rows (8 for EVA, 16 for 384)
                    x_spacing REAL NOT NULL,  -- mm between tips
                    y_spacing REAL NOT NULL,
                    tool_id TEXT NOT NULL,  -- e.g., 'TOOLTYPE:Mca384.Adapter/TOOLNAME:DiTi96.ExtVol'
                    can_mount_tecan_ditis INTEGER NOT NULL,  -- boolean
                    tip_type TEXT,  -- 'MCA96' or 'MCA384'
                    created_at TEXT,
                    updated_at TEXT
                );

                -- Extraction history
                CREATE TABLE IF NOT EXISTS extraction_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_name TEXT NOT NULL,
                    extracted_at TEXT NOT NULL,
                    commands_added INTEGER DEFAULT 0,
                    commands_updated INTEGER DEFAULT 0,
                    sequences_found INTEGER DEFAULT 0,
                    patterns_found INTEGER DEFAULT 0,
                    labware_added INTEGER DEFAULT 0,
                    liquid_classes_added INTEGER DEFAULT 0
                );

                -- Indexes for common queries
                CREATE INDEX IF NOT EXISTS idx_commands_category ON commands(category);
                CREATE INDEX IF NOT EXISTS idx_sequences_from ON sequences(from_command_id);
                CREATE INDEX IF NOT EXISTS idx_sequences_to ON sequences(to_command_id);
                CREATE INDEX IF NOT EXISTS idx_global_values_category ON global_values(category);
                CREATE INDEX IF NOT EXISTS idx_labware_category ON labware(category);
                CREATE INDEX IF NOT EXISTS idx_liquid_classes_device ON liquid_classes(device_type);
                CREATE INDEX IF NOT EXISTS idx_dsl_recipes_object ON dsl_recipes(object_key);
                CREATE INDEX IF NOT EXISTS idx_dsl_recipes_action ON dsl_recipes(action);
                CREATE INDEX IF NOT EXISTS idx_dsl_recipes_active ON dsl_recipes(active);
                CREATE INDEX IF NOT EXISTS idx_rule_evidence_rule ON rule_evidence(rule_name);
                CREATE INDEX IF NOT EXISTS idx_rule_evidence_run ON rule_evidence(run_id);
                CREATE INDEX IF NOT EXISTS idx_worktable_positions_location ON worktable_positions(location);
            """)

    def _migrate_schema(self):
        """Apply migrations to add new columns to existing tables."""
        with self._connection() as conn:
            # Check existing columns in patterns table
            cursor = conn.execute("PRAGMA table_info(patterns)")
            pattern_columns = {row["name"] for row in cursor.fetchall()}

            # Add missing columns to patterns table
            if "parameters" not in pattern_columns:
                conn.execute("ALTER TABLE patterns ADD COLUMN parameters TEXT")
            if "updated_at" not in pattern_columns:
                conn.execute("ALTER TABLE patterns ADD COLUMN updated_at TEXT")

            # Check if rules table exists (it should from _init_schema, but just in case)
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rules'")
            if not cursor.fetchone():
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        rule_type TEXT NOT NULL,
                        category TEXT,
                        protocol_type TEXT,
                        description TEXT NOT NULL,
                        scope TEXT DEFAULT 'global',
                        severity TEXT DEFAULT 'soft',
                        conditions TEXT,
                        requirements TEXT,
                        examples TEXT,
                        source TEXT NOT NULL,
                        source_context TEXT,
                        confidence REAL DEFAULT 0.5,
                        active INTEGER DEFAULT 1,
                        created_at TEXT,
                        updated_at TEXT
                    )
                """)
            else:
                cursor = conn.execute("PRAGMA table_info(rules)")
                rule_columns = {row["name"] for row in cursor.fetchall()}
                if "category" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN category TEXT")
                if "description" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN description TEXT DEFAULT ''")
                if "scope" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN scope TEXT DEFAULT 'global'")
                if "severity" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN severity TEXT DEFAULT 'soft'")
                if "protocol_type" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN protocol_type TEXT")
                if "conditions" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN conditions TEXT")
                if "requirements" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN requirements TEXT")
                if "examples" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN examples TEXT")
                if "source" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN source TEXT DEFAULT 'manual'")
                if "source_context" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN source_context TEXT")
                if "confidence" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN confidence REAL DEFAULT 0.5")
                if "active" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN active INTEGER DEFAULT 1")
                if "created_at" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN created_at TEXT")
                if "updated_at" not in rule_columns:
                    conn.execute("ALTER TABLE rules ADD COLUMN updated_at TEXT")

            # Check if modules table exists
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='modules'")
            if not cursor.fetchone():
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS modules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        domain TEXT DEFAULT 'general',
                        description TEXT NOT NULL,
                        preconditions TEXT,
                        step_template TEXT,
                        constraints TEXT,
                        confidence REAL DEFAULT 0.5,
                        active INTEGER DEFAULT 1,
                        created_at TEXT,
                        updated_at TEXT
                    )
                """)
            else:
                cursor = conn.execute("PRAGMA table_info(modules)")
                module_columns = {row["name"] for row in cursor.fetchall()}
                if "domain" not in module_columns:
                    conn.execute("ALTER TABLE modules ADD COLUMN domain TEXT DEFAULT 'general'")
                if "description" not in module_columns:
                    conn.execute("ALTER TABLE modules ADD COLUMN description TEXT DEFAULT ''")
                if "preconditions" not in module_columns:
                    conn.execute("ALTER TABLE modules ADD COLUMN preconditions TEXT")
                if "step_template" not in module_columns:
                    conn.execute("ALTER TABLE modules ADD COLUMN step_template TEXT")
                if "constraints" not in module_columns:
                    conn.execute("ALTER TABLE modules ADD COLUMN constraints TEXT")
                if "confidence" not in module_columns:
                    conn.execute("ALTER TABLE modules ADD COLUMN confidence REAL DEFAULT 0.5")
                if "active" not in module_columns:
                    conn.execute("ALTER TABLE modules ADD COLUMN active INTEGER DEFAULT 1")
                if "created_at" not in module_columns:
                    conn.execute("ALTER TABLE modules ADD COLUMN created_at TEXT")
                if "updated_at" not in module_columns:
                    conn.execute("ALTER TABLE modules ADD COLUMN updated_at TEXT")

            # Check if rule_evidence table exists
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='rule_evidence'")
            if not cursor.fetchone():
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS rule_evidence (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        rule_name TEXT NOT NULL,
                        protocol_name TEXT,
                        error_code TEXT,
                        error_message TEXT,
                        line_number INTEGER,
                        severity TEXT DEFAULT 'soft',
                        source TEXT DEFAULT 'infopad',
                        created_at TEXT,
                        UNIQUE(run_id, rule_name, error_code, line_number)
                    )
                """)

            # Check if dsl_recipes table exists
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dsl_recipes'")
            if not cursor.fetchone():
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS dsl_recipes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        object_key TEXT NOT NULL,
                        action TEXT NOT NULL,
                        failure_category TEXT,
                        bad_pattern TEXT,
                        good_patterns TEXT NOT NULL,
                        context_text TEXT,
                        tags TEXT,
                        embedding TEXT,
                        active INTEGER DEFAULT 1,
                        created_at TEXT,
                        updated_at TEXT
                    )
                """)
            # Check if worktable_positions table exists
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='worktable_positions'")
            if not cursor.fetchone():
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS worktable_positions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        location TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        frequency INTEGER DEFAULT 1,
                        example_labware TEXT,
                        notes TEXT,
                        created_at TEXT,
                        updated_at TEXT,
                        UNIQUE(location, position)
                    )
                """)

            # Check if adapters table exists
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='adapters'")
            if not cursor.fetchone():
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS adapters (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        display_name TEXT NOT NULL,
                        labware_pattern TEXT NOT NULL,
                        x_count INTEGER NOT NULL,
                        y_count INTEGER NOT NULL,
                        x_spacing REAL NOT NULL,
                        y_spacing REAL NOT NULL,
                        tool_id TEXT NOT NULL,
                        can_mount_tecan_ditis INTEGER NOT NULL,
                        tip_type TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    )
                """)
                # Seed with initial adapter configs
                self._seed_adapters(conn)

            # Ensure indexes added by newer schemas exist for migrated databases
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_scope ON rules(scope)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_protocol_type ON rules(protocol_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_type ON rules(rule_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rules_category ON rules(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_modules_domain ON modules(domain)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_modules_active ON modules(active)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rule_evidence_rule ON rule_evidence(rule_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rule_evidence_run ON rule_evidence(run_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dsl_recipes_object ON dsl_recipes(object_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dsl_recipes_action ON dsl_recipes(action)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_dsl_recipes_active ON dsl_recipes(active)")

    def _seed_adapters(self, conn):
        """Seed the adapters table with initial configurations."""
        now = datetime.now().isoformat()
        adapters = [
            {
                "name": "EVA",
                "display_name": "EVA (Extended Volume)",
                "labware_pattern": "EVA%",
                "x_count": 12,
                "y_count": 8,
                "x_spacing": 9.0,
                "y_spacing": 9.0,
                "tool_id": "TOOLTYPE:Mca384.Adapter/TOOLNAME:DiTi96.ExtVol",
                "can_mount_tecan_ditis": 1,
                "tip_type": "MCA96",
            },
            {
                "name": "384_Combo",
                "display_name": "384 Tips Combo (Partial Tips)",
                "labware_pattern": "384 Tips Combo%",
                "x_count": 24,
                "y_count": 16,
                "x_spacing": 4.5,
                "y_spacing": 4.5,
                "tool_id": "TOOLTYPE:Mca384.Adapter/TOOLNAME:DiTi384.Combo",
                "can_mount_tecan_ditis": 0,
                "tip_type": "MCA384",
            },
        ]
        for adapter in adapters:
            conn.execute("""
                INSERT OR IGNORE INTO adapters
                (name, display_name, labware_pattern, x_count, y_count, x_spacing, y_spacing,
                 tool_id, can_mount_tecan_ditis, tip_type, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                adapter["name"], adapter["display_name"], adapter["labware_pattern"],
                adapter["x_count"], adapter["y_count"], adapter["x_spacing"], adapter["y_spacing"],
                adapter["tool_id"], adapter["can_mount_tecan_ditis"], adapter["tip_type"],
                now, now
            ))

    # =========================================================================
    # COMMAND OPERATIONS
    # =========================================================================

    def upsert_command(self, command: Dict[str, Any]) -> bool:
        """Insert or update a command."""
        now = datetime.now().isoformat()

        with self._connection() as conn:
            # Check if exists
            existing = conn.execute(
                "SELECT id FROM commands WHERE id = ? OR type = ?",
                (command.get("id"), command.get("type"))
            ).fetchone()

            if existing:
                # Update
                conn.execute("""
                    UPDATE commands SET
                        category = COALESCE(?, category),
                        description = COALESCE(?, description),
                        template = COALESCE(?, template),
                        confidence = COALESCE(?, confidence),
                        updated_at = ?
                    WHERE id = ? OR type = ?
                """, (
                    command.get("category"),
                    command.get("description"),
                    command.get("template"),
                    command.get("confidence"),
                    now,
                    command.get("id"),
                    command.get("type")
                ))
                return False  # Updated, not inserted
            else:
                # Insert
                conn.execute("""
                    INSERT INTO commands (id, type, category, description, template, confidence, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    command.get("id"),
                    command.get("type"),
                    command.get("category"),
                    command.get("description"),
                    command.get("template"),
                    command.get("confidence", 1.0),
                    now,
                    now
                ))
                return True  # Inserted

    def get_command(self, command_id: str) -> Optional[Dict]:
        """Get a command by ID."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM commands WHERE id = ?",
                (command_id,)
            ).fetchone()

            if row:
                return dict(row)
        return None

    def get_all_commands(self) -> List[Dict]:
        """Get all commands."""
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM commands ORDER BY category, id").fetchall()
            return [dict(row) for row in rows]

    def get_template(self, command_id: str) -> Optional[str]:
        """Get just the template for a command."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT template FROM commands WHERE id = ?",
                (command_id,)
            ).fetchone()
            return row["template"] if row else None

    # =========================================================================
    # PARAMETER OPERATIONS
    # =========================================================================

    def add_parameter(self, command_id: str, param: Dict[str, Any]) -> int:
        """Add a parameter to a command."""
        with self._connection() as conn:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO parameters (command_id, name, type, required, default_value, description)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                command_id,
                param.get("name"),
                param.get("type"),
                1 if param.get("required") else 0,
                param.get("default"),
                param.get("description")
            ))
            return cursor.lastrowid

    def add_observed_value(self, command_id: str, param_name: str, value: str, source_file: str = None):
        """Record an observed value for a parameter."""
        with self._connection() as conn:
            # Get parameter ID
            param = conn.execute(
                "SELECT id FROM parameters WHERE command_id = ? AND name = ?",
                (command_id, param_name)
            ).fetchone()

            if not param:
                return

            # Upsert observed value
            conn.execute("""
                INSERT INTO observed_values (parameter_id, value, frequency, first_seen, source_file)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(parameter_id, value) DO UPDATE SET
                    frequency = frequency + 1
            """, (param["id"], value, datetime.now().isoformat(), source_file))

    # =========================================================================
    # GLOBAL VALUES (labware, locations, etc.)
    # =========================================================================

    def add_global_value(self, category: str, value: str):
        """Add a global observed value."""
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO global_values (category, value, frequency, first_seen)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(category, value) DO UPDATE SET
                    frequency = frequency + 1
            """, (category, value, datetime.now().isoformat()))

    def get_global_values(self, category: str) -> List[str]:
        """Get all values for a category."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT value FROM global_values WHERE category = ? ORDER BY frequency DESC",
                (category,)
            ).fetchall()
            return [row["value"] for row in rows]

    # =========================================================================
    # LABWARE OPERATIONS
    # =========================================================================

    def upsert_labware(self, labware: Dict[str, Any]) -> bool:
        """Insert or update a labware definition."""
        now = datetime.now().isoformat()
        props = labware.get("properties", {})

        with self._connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(labware)").fetchall()}
            existing = conn.execute(
                "SELECT id FROM labware WHERE name = ?",
                (labware["name"],)
            ).fetchone()

            if existing:
                set_clauses = []
                params = []

                def add_coalesce_clause(col: str, value: Any):
                    if col in columns:
                        safe_col = self._validate_identifier(col)
                        set_clauses.append(f"{safe_col} = COALESCE(?, {safe_col})")
                        params.append(value)

                def add_set_clause(col: str, value: Any):
                    if col in columns:
                        safe_col = self._validate_identifier(col)
                        set_clauses.append(f"{safe_col} = ?")
                        params.append(value)

                add_coalesce_clause("category", labware.get("category"))
                add_coalesce_clause("functional_group", labware.get("functional_group"))
                add_coalesce_clause("wells", props.get("wells"))
                add_coalesce_clause("rows", props.get("rows"))
                add_coalesce_clause("columns", props.get("columns"))
                add_coalesce_clause("x_spacing", props.get("x_spacing"))
                add_coalesce_clause("y_spacing", props.get("y_spacing"))
                add_coalesce_clause("properties", json.dumps(props) if props else None)
                add_set_clause("updated_at", now)

                if set_clauses:
                    params.append(labware["name"])
                    conn.execute(
                        f"UPDATE labware SET {', '.join(set_clauses)} WHERE name = ?",
                        params
                    )
                return False
            else:
                insert_cols = [self._validate_identifier("name")]
                insert_vals = [labware["name"]]

                def add_insert(col: str, value: Any):
                    if col in columns:
                        insert_cols.append(self._validate_identifier(col))
                        insert_vals.append(value)

                add_insert("category", labware.get("category", "unknown"))
                add_insert("functional_group", labware.get("functional_group"))
                add_insert("wells", props.get("wells"))
                add_insert("rows", props.get("rows"))
                add_insert("columns", props.get("columns"))
                add_insert("x_spacing", props.get("x_spacing"))
                add_insert("y_spacing", props.get("y_spacing"))
                add_insert("properties", json.dumps(props) if props else None)
                add_insert("source_file", labware.get("source_file"))
                add_insert("created_at", now)
                add_insert("updated_at", now)

                placeholders = ", ".join(["?"] * len(insert_cols))
                conn.execute(
                    f"INSERT INTO labware ({', '.join(insert_cols)}) VALUES ({placeholders})",
                    insert_vals
                )
                return True

    def get_labware(self, name: str) -> Optional[Dict]:
        """Get labware by name."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM labware WHERE name = ?",
                (name,)
            ).fetchone()
            if row:
                result = dict(row)
                if result.get("properties"):
                    result["properties"] = json.loads(result["properties"])
                return result
        return None

    def get_all_labware(self, category: str = None) -> List[Dict]:
        """Get all labware, optionally filtered by category."""
        with self._connection() as conn:
            if category:
                rows = conn.execute(
                    "SELECT * FROM labware WHERE category = ? ORDER BY name",
                    (category,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM labware ORDER BY category, name").fetchall()

            results = []
            for row in rows:
                result = dict(row)
                if result.get("properties"):
                    result["properties"] = json.loads(result["properties"])
                results.append(result)
            return results

    def get_labware_by_category(self) -> Dict[str, List[Dict]]:
        """Get all labware grouped by category."""
        labware = self.get_all_labware()
        by_category = {}
        for lw in labware:
            cat = lw.get("category", "unknown")
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(lw)
        return by_category

    # =========================================================================
    # LIQUID CLASS OPERATIONS
    # =========================================================================

    def upsert_liquid_class(self, lc: Dict[str, Any]) -> bool:
        """Insert or update a liquid class definition."""
        now = datetime.now().isoformat()

        # Handle both formats: extractor outputs "parameters", DB uses key/all_parameters
        all_params = lc.get("all_parameters") or lc.get("parameters", {})
        key_params = lc.get("key_parameters", {})

        # If no key_params provided, extract key ones from all_params
        if not key_params and all_params:
            key_params = {
                k: v for k, v in all_params.items()
                if k in ("aspirationSpeed", "dispenseSpeed", "AirSpeed",
                         "aspirationAcceleration", "dispenseAcceleration")
            }

        conditions = lc.get("conditions", [])

        with self._connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(liquid_classes)").fetchall()}
            key_cols = None
            try:
                indexes = conn.execute("PRAGMA index_list(liquid_classes)").fetchall()
                for idx in indexes:
                    if idx["unique"]:
                        idx_cols = [row["name"] for row in conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()]
                        if "name" in idx_cols:
                            key_cols = [c for c in idx_cols if c in columns]
                            break
            except Exception:
                key_cols = None

            if not key_cols:
                key_cols = ["name", "device_type"] if "device_type" in columns else ["name"]
            key_cols = [c for c in key_cols if c in columns]
            if not key_cols:
                key_cols = ["name"]

            key_values = []
            safe_key_cols = [self._validate_identifier(c) for c in key_cols]
            for col in safe_key_cols:
                if col == "name":
                    key_values.append(lc["name"])
                elif col == "device_type":
                    key_values.append(lc.get("device_type", "unknown"))

            where_clause = " AND ".join([f"{col} = ?" for col in safe_key_cols])
            existing = conn.execute(
                f"SELECT id FROM liquid_classes WHERE {where_clause}",
                key_values
            ).fetchone()

            if existing:
                set_clauses = []
                params = []

                def add_coalesce_clause(col: str, value: Any):
                    if col in columns:
                        safe_col = self._validate_identifier(col)
                        set_clauses.append(f"{safe_col} = COALESCE(?, {safe_col})")
                        params.append(value)

                def add_set_clause(col: str, value: Any):
                    if col in columns:
                        safe_col = self._validate_identifier(col)
                        set_clauses.append(f"{safe_col} = ?")
                        params.append(value)

                add_coalesce_clause("description", lc.get("description"))
                add_coalesce_clause("aspiration_speed", key_params.get("aspirationSpeed"))
                add_coalesce_clause("dispense_speed", key_params.get("dispenseSpeed"))
                add_coalesce_clause("key_parameters", json.dumps(key_params) if key_params else None)
                add_coalesce_clause("all_parameters", json.dumps(all_params) if all_params else None)
                add_coalesce_clause("conditions", json.dumps(conditions) if conditions else None)
                add_set_clause("updated_at", now)

                if set_clauses:
                    params.extend(key_values)
                    conn.execute(
                        f"UPDATE liquid_classes SET {', '.join(set_clauses)} WHERE {where_clause}",
                        params
                    )
                return False
            else:
                insert_cols = [self._validate_identifier("name"), self._validate_identifier("device_type")]
                insert_vals = [lc["name"], lc.get("device_type", "unknown")]

                def add_insert(col: str, value: Any):
                    if col in columns:
                        insert_cols.append(self._validate_identifier(col))
                        insert_vals.append(value)

                add_insert("description", lc.get("description"))
                add_insert("aspiration_speed", key_params.get("aspirationSpeed"))
                add_insert("dispense_speed", key_params.get("dispenseSpeed"))
                add_insert("key_parameters", json.dumps(key_params) if key_params else None)
                add_insert("all_parameters", json.dumps(all_params) if all_params else None)
                add_insert("conditions", json.dumps(conditions) if conditions else None)
                add_insert("source_file", lc.get("source_file"))
                add_insert("created_at", now)
                add_insert("updated_at", now)

                placeholders = ", ".join(["?"] * len(insert_cols))
                conn.execute(
                    f"INSERT INTO liquid_classes ({', '.join(insert_cols)}) VALUES ({placeholders})",
                    insert_vals
                )
                return True

    def get_liquid_class(self, name: str, device_type: str = None) -> Optional[Dict]:
        """Get liquid class by name and optionally device type."""
        with self._connection() as conn:
            if device_type:
                row = conn.execute(
                    "SELECT * FROM liquid_classes WHERE name = ? AND device_type = ?",
                    (name, device_type)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM liquid_classes WHERE name = ?",
                    (name,)
                ).fetchone()
            if row:
                result = dict(row)
                for field in ["key_parameters", "all_parameters", "conditions"]:
                    if result.get(field):
                        result[field] = json.loads(result[field])
                return result
        return None

    def get_all_liquid_classes(self, device_type: str = None) -> List[Dict]:
        """Get all liquid classes, optionally filtered by device type."""
        with self._connection() as conn:
            if device_type:
                rows = conn.execute(
                    "SELECT * FROM liquid_classes WHERE device_type = ? ORDER BY name",
                    (device_type,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM liquid_classes ORDER BY device_type, name").fetchall()

            results = []
            for row in rows:
                result = dict(row)
                for field in ["key_parameters", "all_parameters", "conditions"]:
                    if result.get(field):
                        result[field] = json.loads(result[field])
                results.append(result)
            return results

    def get_liquid_classes_by_device(self) -> Dict[str, List[Dict]]:
        """Get all liquid classes grouped by device type."""
        classes = self.get_all_liquid_classes()
        by_device = {}
        for lc in classes:
            device = lc.get("device_type", "unknown")
            if device not in by_device:
                by_device[device] = []
            by_device[device].append(lc)
        return by_device

    # =========================================================================
    # SEQUENCE OPERATIONS (Logic Extraction)
    # =========================================================================

    def record_sequence(self, from_cmd: str, to_cmd: str, context: Dict = None):
        """Record that one command follows another."""
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO sequences (from_command_id, to_command_id, frequency, contexts)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(from_command_id, to_command_id) DO UPDATE SET
                    frequency = frequency + 1
            """, (from_cmd, to_cmd, json.dumps(context) if context else None))

    def get_next_commands(self, command_id: str, limit: int = 5) -> List[Dict]:
        """Get most common commands that follow a given command."""
        with self._connection() as conn:
            rows = conn.execute("""
                SELECT to_command_id, frequency
                FROM sequences
                WHERE from_command_id = ?
                ORDER BY frequency DESC
                LIMIT ?
            """, (command_id, limit)).fetchall()
            return [{"command": row["to_command_id"], "frequency": row["frequency"]} for row in rows]

    def get_sequence_graph(self) -> List[Dict]:
        """Get all sequences as a graph edge list."""
        with self._connection() as conn:
            rows = conn.execute("""
                SELECT from_command_id, to_command_id, frequency
                FROM sequences
                ORDER BY frequency DESC
            """).fetchall()
            return [dict(row) for row in rows]

    # =========================================================================
    # PATTERN OPERATIONS
    # =========================================================================

    def upsert_pattern(self, pattern: Dict[str, Any]) -> bool:
        """
        Insert or update a pattern.

        Args:
            pattern: Dict with keys: name, description, pattern_type, steps, parameters

        Returns:
            True if inserted, False if updated

        Raises:
            ValueError: If required fields are missing
            Exception: If database operation fails
        """
        # Validate required fields
        if "name" not in pattern:
            raise ValueError("Pattern must have a 'name' field")
        if "steps" not in pattern:
            raise ValueError("Pattern must have a 'steps' field")

        try:
            now = datetime.now().isoformat()

            with self._connection() as conn:
                existing = conn.execute(
                    "SELECT id FROM patterns WHERE name = ?",
                    (pattern["name"],)
                ).fetchone()

                if existing:
                    conn.execute("""
                        UPDATE patterns SET
                            description = COALESCE(?, description),
                            pattern_type = COALESCE(?, pattern_type),
                            steps = COALESCE(?, steps),
                            parameters = COALESCE(?, parameters),
                            frequency = frequency + 1,
                            confidence = MIN(1.0, confidence + 0.1),
                            updated_at = ?
                        WHERE name = ?
                    """, (
                        pattern.get("description"),
                        pattern.get("pattern_type"),
                        json.dumps(pattern["steps"]) if pattern.get("steps") else None,
                        json.dumps(pattern["parameters"]) if pattern.get("parameters") else None,
                        now,
                        pattern["name"]
                    ))
                    return False
                else:
                    conn.execute("""
                        INSERT INTO patterns (name, description, pattern_type, steps, parameters,
                                             frequency, confidence, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 1, 0.5, ?, ?)
                    """, (
                        pattern["name"],
                        pattern.get("description"),
                        pattern.get("pattern_type", "sequence"),
                        json.dumps(pattern["steps"]),
                        json.dumps(pattern["parameters"]) if pattern.get("parameters") else None,
                        now,
                        now
                    ))
                    return True
        except ValueError:
            # Re-raise validation errors as-is
            raise
        except Exception as e:
            # Log and re-raise database errors with context
            print(f"ERROR in upsert_pattern for '{pattern.get('name', 'unknown')}': {e}")
            raise

    def save_pattern(self, name: str, steps: List[str], description: str = None,
                     pattern_type: str = "sequence", parameters: Dict = None):
        """Save a named pattern (convenience wrapper)."""
        return self.upsert_pattern({
            "name": name,
            "description": description,
            "pattern_type": pattern_type,
            "steps": steps,
            "parameters": parameters
        })

    def get_pattern(self, name: str) -> Optional[Dict]:
        """Get a pattern by name."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM patterns WHERE name = ?",
                (name,)
            ).fetchone()
            if row:
                result = dict(row)
                result["steps"] = json.loads(result["steps"]) if result.get("steps") else []
                if result.get("parameters"):
                    result["parameters"] = json.loads(result["parameters"])
                return result
        return None

    def get_all_patterns(self) -> List[Dict]:
        """Get all patterns."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM patterns ORDER BY frequency DESC, name"
            ).fetchall()
            results = []
            for row in rows:
                result = dict(row)
                result["steps"] = json.loads(result["steps"]) if result.get("steps") else []
                if result.get("parameters"):
                    result["parameters"] = json.loads(result["parameters"])
                results.append(result)
            return results

    def find_patterns(self, min_frequency: int = 2) -> List[Dict]:
        """Find all patterns with minimum frequency."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM patterns WHERE frequency >= ? ORDER BY frequency DESC",
                (min_frequency,)
            ).fetchall()
            results = []
            for row in rows:
                result = dict(row)
                result["steps"] = json.loads(result["steps"]) if result.get("steps") else []
                if result.get("parameters"):
                    result["parameters"] = json.loads(result["parameters"])
                results.append(result)
            return results

    def get_patterns_by_type(self, pattern_type: str) -> List[Dict]:
        """Get patterns by type (sequence, loop, conditional)."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM patterns WHERE pattern_type = ? ORDER BY frequency DESC",
                (pattern_type,)
            ).fetchall()
            results = []
            for row in rows:
                result = dict(row)
                result["steps"] = json.loads(result["steps"]) if result.get("steps") else []
                if result.get("parameters"):
                    result["parameters"] = json.loads(result["parameters"])
                results.append(result)
            return results

    def delete_pattern(self, name: str) -> bool:
        """Delete a pattern by name."""
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM patterns WHERE name = ?", (name,))
            return cursor.rowcount > 0

    # =========================================================================
    # RULES OPERATIONS
    # =========================================================================

    def upsert_rule(self, rule: Dict[str, Any]) -> bool:
        """
        Insert or update a rule.

        Args:
            rule: Dict with keys: name, rule_type, category, description,
                  conditions, requirements, examples, source, source_context, confidence

        Returns:
            True if inserted, False if updated
        """
        now = datetime.now().isoformat()

        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id FROM rules WHERE name = ?",
                (rule["name"],)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE rules SET
                        rule_type = COALESCE(?, rule_type),
                        category = COALESCE(?, category),
                        protocol_type = COALESCE(?, protocol_type),
                        description = COALESCE(?, description),
                        scope = COALESCE(?, scope),
                        severity = COALESCE(?, severity),
                        conditions = COALESCE(?, conditions),
                        requirements = COALESCE(?, requirements),
                        examples = COALESCE(?, examples),
                        source = COALESCE(?, source),
                        source_context = COALESCE(?, source_context),
                        confidence = COALESCE(?, confidence),
                        updated_at = ?
                    WHERE name = ?
                """, (
                    rule.get("rule_type"),
                    rule.get("category"),
                    rule.get("protocol_type"),
                    rule.get("description"),
                    rule.get("scope"),
                    rule.get("severity"),
                    json.dumps(rule["conditions"]) if rule.get("conditions") else None,
                    json.dumps(rule["requirements"]) if rule.get("requirements") else None,
                    json.dumps(rule["examples"]) if rule.get("examples") else None,
                    rule.get("source"),
                    rule.get("source_context"),
                    rule.get("confidence"),
                    now,
                    rule["name"]
                ))
                return False
            else:
                conn.execute("""
                    INSERT INTO rules (name, rule_type, category, protocol_type, description,
                                      scope, severity,
                                      conditions, requirements, examples,
                                      source, source_context, confidence,
                                      active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """, (
                    rule["name"],
                    rule.get("rule_type", "general"),
                    rule.get("category"),
                    rule.get("protocol_type"),
                    rule["description"],
                    rule.get("scope", "global"),
                    rule.get("severity", "soft"),
                    json.dumps(rule["conditions"]) if rule.get("conditions") else None,
                    json.dumps(rule["requirements"]) if rule.get("requirements") else None,
                    json.dumps(rule["examples"]) if rule.get("examples") else None,
                    rule.get("source", "manual"),
                    rule.get("source_context"),
                    rule.get("confidence", 0.5),
                    now,
                    now
                ))
                return True

    def get_rule(self, name: str) -> Optional[Dict]:
        """Get a rule by name."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM rules WHERE name = ?",
                (name,)
            ).fetchone()

            if row:
                result = dict(row)
                for field in ["conditions", "requirements", "examples"]:
                    if result.get(field):
                        try:
                            result[field] = json.loads(result[field])
                        except (TypeError, json.JSONDecodeError):
                            # Backward compatibility: keep legacy plain-text content as-is.
                            pass
                return result
        return None

    def get_all_rules(self, active_only: bool = True, protocol_type: Optional[str] = None) -> List[Dict]:
        """Get rules with optional active/protocol-type filtering.

        protocol_type filter semantics:
        - include rules with protocol_type == requested type
        - include global rules where protocol_type IS NULL
        - exclude rules tagged for other protocol types
        """
        with self._connection() as conn:
            params: list[Any] = []
            where: list[str] = []
            if active_only:
                where.append("active = 1")
            if protocol_type:
                where.append("(protocol_type IS NULL OR protocol_type = ?)")
                params.append(protocol_type)
            where_sql = f" WHERE {' AND '.join(where)}" if where else ""
            if active_only:
                rows = conn.execute(
                    f"SELECT * FROM rules{where_sql} ORDER BY rule_type, category, name",
                    params
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT * FROM rules{where_sql} ORDER BY rule_type, category, name",
                    params
                ).fetchall()

            results = []
            for row in rows:
                result = dict(row)
                for field in ["conditions", "requirements", "examples"]:
                    if result.get(field):
                        try:
                            result[field] = json.loads(result[field])
                        except (TypeError, json.JSONDecodeError):
                            # Some user-entered rules store human text, not JSON.
                            pass
                results.append(result)
            return results

    def get_rules_by_type(self, rule_type: str, protocol_type: Optional[str] = None) -> List[Dict]:
        """Get active rules of a specific type with optional protocol-type filtering."""
        with self._connection() as conn:
            sql = "SELECT * FROM rules WHERE rule_type = ? AND active = 1"
            params: list[Any] = [rule_type]
            if protocol_type:
                sql += " AND (protocol_type IS NULL OR protocol_type = ?)"
                params.append(protocol_type)
            sql += " ORDER BY category, name"
            rows = conn.execute(
                sql,
                params
            ).fetchall()

            results = []
            for row in rows:
                result = dict(row)
                for field in ["conditions", "requirements", "examples"]:
                    if result.get(field):
                        try:
                            result[field] = json.loads(result[field])
                        except (TypeError, json.JSONDecodeError):
                            pass
                results.append(result)
            return results

    def get_rules_by_category(self, category: str, protocol_type: Optional[str] = None) -> List[Dict]:
        """Get active rules in a specific category with optional protocol-type filtering."""
        with self._connection() as conn:
            sql = "SELECT * FROM rules WHERE category = ? AND active = 1"
            params: list[Any] = [category]
            if protocol_type:
                sql += " AND (protocol_type IS NULL OR protocol_type = ?)"
                params.append(protocol_type)
            sql += " ORDER BY rule_type, name"
            rows = conn.execute(
                sql,
                params
            ).fetchall()

            results = []
            for row in rows:
                result = dict(row)
                for field in ["conditions", "requirements", "examples"]:
                    if result.get(field):
                        try:
                            result[field] = json.loads(result[field])
                        except (TypeError, json.JSONDecodeError):
                            pass
                results.append(result)
            return results

    def get_rules_grouped(self) -> Dict[str, Dict[str, List[Dict]]]:
        """Get all active rules grouped by type and category."""
        rules = self.get_all_rules(active_only=True)
        grouped = {}

        for rule in rules:
            rule_type = rule.get("rule_type", "general")
            category = rule.get("category", "general")

            if rule_type not in grouped:
                grouped[rule_type] = {}
            if category not in grouped[rule_type]:
                grouped[rule_type][category] = []

            grouped[rule_type][category].append(rule)

        return grouped

    def deactivate_rule(self, name: str) -> bool:
        """Deactivate a rule (soft delete)."""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE rules SET active = 0, updated_at = ? WHERE name = ?",
                (datetime.now().isoformat(), name)
            )
            return cursor.rowcount > 0

    def activate_rule(self, name: str) -> bool:
        """Reactivate a rule."""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE rules SET active = 1, updated_at = ? WHERE name = ?",
                (datetime.now().isoformat(), name)
            )
            return cursor.rowcount > 0

    # =========================================================================
    # MODULES OPERATIONS
    # =========================================================================

    def upsert_module(self, module: Dict[str, Any]) -> bool:
        """
        Insert or update a semantic module definition.

        Returns:
            True if inserted, False if updated.
        """
        now = datetime.now().isoformat()
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id FROM modules WHERE name = ?",
                (module["name"],)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE modules SET
                        domain = COALESCE(?, domain),
                        description = COALESCE(?, description),
                        preconditions = COALESCE(?, preconditions),
                        step_template = COALESCE(?, step_template),
                        constraints = COALESCE(?, constraints),
                        confidence = COALESCE(?, confidence),
                        active = COALESCE(?, active),
                        updated_at = ?
                    WHERE name = ?
                """, (
                    module.get("domain"),
                    module.get("description"),
                    json.dumps(module["preconditions"]) if module.get("preconditions") is not None else None,
                    json.dumps(module["step_template"]) if module.get("step_template") is not None else None,
                    json.dumps(module["constraints"]) if module.get("constraints") is not None else None,
                    module.get("confidence"),
                    module.get("active"),
                    now,
                    module["name"],
                ))
                return False

            conn.execute("""
                INSERT INTO modules (
                    name, domain, description, preconditions, step_template,
                    constraints, confidence, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                module["name"],
                module.get("domain", "general"),
                module.get("description", ""),
                json.dumps(module["preconditions"]) if module.get("preconditions") is not None else None,
                json.dumps(module["step_template"]) if module.get("step_template") is not None else None,
                json.dumps(module["constraints"]) if module.get("constraints") is not None else None,
                module.get("confidence", 0.5),
                module.get("active", 1),
                now,
                now,
            ))
            return True

    def get_module(self, name: str) -> Optional[Dict]:
        """Get one module by name."""
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM modules WHERE name = ?", (name,)).fetchone()
            if not row:
                return None
            result = dict(row)
            for field in ["preconditions", "step_template", "constraints"]:
                if result.get(field):
                    try:
                        result[field] = json.loads(result[field])
                    except (TypeError, json.JSONDecodeError):
                        pass
            return result

    def get_all_modules(self, active_only: bool = True) -> List[Dict]:
        """Get all module definitions."""
        with self._connection() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM modules WHERE active = 1 ORDER BY domain, name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM modules ORDER BY domain, name"
                ).fetchall()
            results = []
            for row in rows:
                result = dict(row)
                for field in ["preconditions", "step_template", "constraints"]:
                    if result.get(field):
                        try:
                            result[field] = json.loads(result[field])
                        except (TypeError, json.JSONDecodeError):
                            pass
                results.append(result)
            return results

    def deactivate_module(self, name: str) -> bool:
        """Deactivate a module (soft delete)."""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE modules SET active = 0, updated_at = ? WHERE name = ?",
                (datetime.now().isoformat(), name),
            )
            return cursor.rowcount > 0

    def activate_module(self, name: str) -> bool:
        """Reactivate a module."""
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE modules SET active = 1, updated_at = ? WHERE name = ?",
                (datetime.now().isoformat(), name),
            )
            return cursor.rowcount > 0

    # =========================================================================
    # RULE EVIDENCE OPERATIONS
    # =========================================================================

    def upsert_rule_evidence(self, evidence: Dict[str, Any]) -> bool:
        """
        Insert a rule evidence record.

        Returns:
            True if inserted, False if duplicate row existed.
        """
        now = datetime.now().isoformat()
        with self._connection() as conn:
            existing = conn.execute(
                """
                SELECT id FROM rule_evidence
                WHERE run_id = ? AND rule_name = ? AND error_code = ? AND COALESCE(line_number, -1) = COALESCE(?, -1)
                """,
                (
                    evidence.get("run_id"),
                    evidence.get("rule_name"),
                    evidence.get("error_code"),
                    evidence.get("line_number"),
                ),
            ).fetchone()
            if existing:
                return False

            conn.execute(
                """
                INSERT INTO rule_evidence (
                    run_id, rule_name, protocol_name, error_code, error_message,
                    line_number, severity, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.get("run_id"),
                    evidence.get("rule_name"),
                    evidence.get("protocol_name"),
                    evidence.get("error_code"),
                    evidence.get("error_message"),
                    evidence.get("line_number"),
                    evidence.get("severity", "soft"),
                    evidence.get("source", "infopad"),
                    now,
                ),
            )
            return True

    def get_rule_evidence(self, rule_name: Optional[str] = None, limit: int = 200) -> List[Dict]:
        """Get evidence rows, optionally filtered by rule name."""
        with self._connection() as conn:
            if rule_name:
                rows = conn.execute(
                    """
                    SELECT * FROM rule_evidence
                    WHERE rule_name = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (rule_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM rule_evidence
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    # =========================================================================
    # WORKTABLE POSITIONS OPERATIONS
    # =========================================================================

    def upsert_worktable_position(self, location: str, position: int,
                                   example_labware: str = None, notes: str = None) -> bool:
        """
        Insert or update a worktable position.

        Args:
            location: Location name (e.g., 'MCA384_Diti_ActiveNest')
            position: Position number (e.g., 1, 2)
            example_labware: Example of labware that uses this position
            notes: Additional notes

        Returns:
            True if inserted, False if updated (frequency incremented)
        """
        now = datetime.now().isoformat()

        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id FROM worktable_positions WHERE location = ? AND position = ?",
                (location, position)
            ).fetchone()

            if existing:
                conn.execute("""
                    UPDATE worktable_positions SET
                        frequency = frequency + 1,
                        example_labware = COALESCE(?, example_labware),
                        notes = COALESCE(?, notes),
                        updated_at = ?
                    WHERE location = ? AND position = ?
                """, (example_labware, notes, now, location, position))
                return False
            else:
                conn.execute("""
                    INSERT INTO worktable_positions (location, position, frequency,
                                                    example_labware, notes, created_at, updated_at)
                    VALUES (?, ?, 1, ?, ?, ?, ?)
                """, (location, position, example_labware, notes, now, now))
                return True

    def get_valid_positions(self, location: str) -> List[int]:
        """Get all valid positions for a location."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT position FROM worktable_positions WHERE location = ? ORDER BY position",
                (location,)
            ).fetchall()
            return [row["position"] for row in rows]

    def get_all_locations(self) -> List[str]:
        """Get all known locations."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT location FROM worktable_positions ORDER BY location"
            ).fetchall()
            return [row["location"] for row in rows]

    def get_worktable_positions_map(self) -> Dict[str, List[int]]:
        """Get all worktable positions as a map of location -> positions."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT location, position FROM worktable_positions ORDER BY location, position"
            ).fetchall()

            result = {}
            for row in rows:
                loc = row["location"]
                pos = row["position"]
                if loc not in result:
                    result[loc] = []
                result[loc].append(pos)
            return result

    def get_worktable_positions_detailed(self) -> List[Dict]:
        """Get all worktable positions with full details."""
        with self._connection() as conn:
            rows = conn.execute(
                """SELECT location, position, frequency, example_labware, notes
                   FROM worktable_positions ORDER BY location, position"""
            ).fetchall()
            return [dict(row) for row in rows]

    def is_valid_position(self, location: str, position: int) -> bool:
        """Check if a position is valid for a location."""
        valid_positions = self.get_valid_positions(location)
        if not valid_positions:
            # Location unknown - allow any position (will be warned elsewhere)
            return True
        return position in valid_positions

    # =========================================================================
    # ADAPTER OPERATIONS
    # =========================================================================

    def get_adapter_by_name(self, name: str) -> Optional[Dict]:
        """Get adapter configuration by name."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM adapters WHERE name = ?",
                (name,)
            ).fetchone()
            return dict(row) if row else None

    def get_adapter_by_labware(self, labware_name: str) -> Optional[Dict]:
        """
        Get adapter configuration by matching labware name.

        Uses SQL LIKE pattern matching against labware_pattern.

        Args:
            labware_name: The labware name from get_head_adapter step (e.g., 'EVA[001]')

        Returns:
            Adapter config dict or None if no match
        """
        with self._connection() as conn:
            # Use LIKE for pattern matching (patterns use SQL % wildcard)
            rows = conn.execute(
                "SELECT * FROM adapters WHERE ? LIKE labware_pattern",
                (labware_name,)
            ).fetchall()
            if rows:
                return dict(rows[0])
            return None

    def get_all_adapters(self) -> List[Dict]:
        """Get all adapter configurations."""
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM adapters ORDER BY name").fetchall()
            return [dict(row) for row in rows]

    def get_adapter_config(self, labware_name: str) -> Dict:
        """
        Get adapter configuration with computed defaults.

        Returns a dict with all values needed for template filling:
        - display_name, x_count, y_count, x_spacing, y_spacing
        - tool_id, can_mount_tecan_ditis
        - partial_columns (defaults to x_count)
        - partial_rows (defaults to y_count)

        Args:
            labware_name: Labware name from get_head_adapter

        Returns:
            Config dict with defaults, or default 384 Combo config if not found
        """
        adapter = self.get_adapter_by_labware(labware_name)

        if adapter:
            return {
                "name": adapter["name"],
                "display_name": adapter["display_name"],
                "x_count": adapter["x_count"],
                "y_count": adapter["y_count"],
                "x_spacing": adapter["x_spacing"],
                "y_spacing": adapter["y_spacing"],
                "tool_id": adapter["tool_id"],
                "can_mount_tecan_ditis": bool(adapter["can_mount_tecan_ditis"]),
                "tip_type": adapter["tip_type"],
                # Defaults for partial pickup (max values)
                "partial_columns": adapter["x_count"],
                "partial_rows": adapter["y_count"],
                "last_tip_x": adapter["x_count"],
                "last_tip_y": adapter["y_count"],
            }

        # Default to 384 Combo if no match (backwards compatibility)
        return {
            "name": "384_Combo",
            "display_name": "384 Tips Combo (Partial Tips)",
            "x_count": 24,
            "y_count": 16,
            "x_spacing": 4.5,
            "y_spacing": 4.5,
            "tool_id": "TOOLTYPE:Mca384.Adapter/TOOLNAME:DiTi384.Combo",
            "can_mount_tecan_ditis": False,
            "tip_type": "MCA384",
            "partial_columns": 24,
            "partial_rows": 16,
            "last_tip_x": 24,
            "last_tip_y": 16,
        }

    def upsert_adapter(self, adapter: Dict[str, Any]) -> bool:
        """Insert or update an adapter configuration from install-grounded metadata."""
        now = datetime.now().isoformat()

        def _first_text(value, default: str = "") -> str:
            if isinstance(value, (list, tuple)):
                for item in value:
                    if item not in (None, ""):
                        return str(item)
                return default
            if value in (None, ""):
                return default
            return str(value)

        def _first_int(value, default: int) -> int:
            if isinstance(value, (list, tuple)):
                for item in value:
                    try:
                        return int(float(item))
                    except (TypeError, ValueError):
                        continue
                return default
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return default

        def _first_float(value, default: float) -> float:
            if isinstance(value, (list, tuple)):
                for item in value:
                    try:
                        return float(item)
                    except (TypeError, ValueError):
                        continue
                return default
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        name = _first_text(adapter.get("name"))
        if not name:
            raise ValueError("Adapter payload must include a non-empty name")

        display_name = _first_text(adapter.get("display_name") or adapter.get("display_names"), name)
        labware_names = adapter.get("labware_names") or []
        primary_labware = _first_text(labware_names, name)
        pattern_base = primary_labware.split("[", 1)[0].strip() if primary_labware else name
        labware_pattern = adapter.get("labware_pattern") or (
            f"{pattern_base}%"
            if pattern_base and "%" not in pattern_base and "*" not in pattern_base
            else pattern_base.replace("*", "%")
        )
        tip_type = _first_text(adapter.get("tip_type") or adapter.get("tip_types"), "MCA384")
        tool_id = _first_text(adapter.get("tool_id"), "")
        can_mount_raw = _first_text(adapter.get("can_mount_tecan_ditis"), "False").lower()
        can_mount_tecan_ditis = 1 if can_mount_raw in {"true", "1", "yes"} else 0

        payload = {
            "name": name,
            "display_name": display_name,
            "labware_pattern": labware_pattern or f"{name}%",
            "x_count": _first_int(adapter.get("x_count") or adapter.get("x_counts"), 24),
            "y_count": _first_int(adapter.get("y_count") or adapter.get("y_counts"), 16),
            "x_spacing": _first_float(adapter.get("x_spacing") or adapter.get("x_spacings"), 4.5),
            "y_spacing": _first_float(adapter.get("y_spacing") or adapter.get("y_spacings"), 4.5),
            "tool_id": tool_id,
            "can_mount_tecan_ditis": can_mount_tecan_ditis,
            "tip_type": tip_type,
        }

        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id FROM adapters WHERE name = ?",
                (payload["name"],)
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE adapters SET
                        display_name = ?,
                        labware_pattern = ?,
                        x_count = ?,
                        y_count = ?,
                        x_spacing = ?,
                        y_spacing = ?,
                        tool_id = ?,
                        can_mount_tecan_ditis = ?,
                        tip_type = ?,
                        updated_at = ?
                    WHERE name = ?
                    """,
                    (
                        payload["display_name"],
                        payload["labware_pattern"],
                        payload["x_count"],
                        payload["y_count"],
                        payload["x_spacing"],
                        payload["y_spacing"],
                        payload["tool_id"],
                        payload["can_mount_tecan_ditis"],
                        payload["tip_type"],
                        now,
                        payload["name"],
                    ),
                )
                return False

            conn.execute(
                """
                INSERT INTO adapters (
                    name, display_name, labware_pattern, x_count, y_count, x_spacing,
                    y_spacing, tool_id, can_mount_tecan_ditis, tip_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["name"],
                    payload["display_name"],
                    payload["labware_pattern"],
                    payload["x_count"],
                    payload["y_count"],
                    payload["x_spacing"],
                    payload["y_spacing"],
                    payload["tool_id"],
                    payload["can_mount_tecan_ditis"],
                    payload["tip_type"],
                    now,
                    now,
                ),
            )
            return True

    def ingest_install_bundle(self, bundle: Any) -> Dict[str, int]:
        """Merge a shared FluentControl install bundle into the local DB."""

        def _bundle_attr(obj: Any, name: str, default):
            if isinstance(obj, dict):
                return obj.get(name, default)
            return getattr(obj, name, default)

        workspaces = list(_bundle_attr(bundle, "workspaces", []))
        liquid_catalog = _bundle_attr(bundle, "liquid_catalog", {}) or {}
        script_bundle = _bundle_attr(bundle, "script_bundle", {}) or {}
        position_inventory = list(
            _bundle_attr(bundle, "position_inventory", script_bundle.get("position_inventory", []))
        )
        adapter_inventory = list(
            _bundle_attr(bundle, "adapter_inventory", script_bundle.get("adapter_inventory", []))
        )
        device_inventory = script_bundle.get("device_inventory", {}) or {}
        macro_signatures = script_bundle.get("macro_signatures", []) or []

        summary = {
            "worktable_positions_inserted": 0,
            "worktable_positions_updated": 0,
            "liquid_classes_inserted": 0,
            "liquid_classes_updated": 0,
            "adapters_inserted": 0,
            "adapters_updated": 0,
            "global_values_added": 0,
        }

        for item in position_inventory:
            location = str(item.get("location") or "").strip()
            if not location:
                continue
            positions = list(item.get("positions") or [1])
            example_labware = next(
                (
                    str(value)
                    for value in (item.get("example_labware_labels") or item.get("example_labware_names") or [])
                    if value
                ),
                None,
            )
            notes = ", ".join(item.get("workspace_names") or [])
            for position in positions or [1]:
                try:
                    position_number = int(position)
                except (TypeError, ValueError):
                    continue
                inserted = self.upsert_worktable_position(
                    location,
                    position_number,
                    example_labware=example_labware,
                    notes=notes or None,
                )
                summary[
                    "worktable_positions_inserted" if inserted else "worktable_positions_updated"
                ] += 1
            self.add_global_value("locations", location)
            summary["global_values_added"] += 1

        for workspace in workspaces:
            for labware_name in workspace.get("labware_names", []):
                if not labware_name:
                    continue
                self.add_global_value("workspace_labware", str(labware_name))
                summary["global_values_added"] += 1

        for liquid_class in liquid_catalog.get("liquid_classes", []):
            name = liquid_class.get("object_name")
            if not name:
                continue
            inserted = self.upsert_liquid_class(
                {
                    "name": name,
                    "device_type": liquid_class.get("device_type", "unknown"),
                    "description": liquid_class.get("comment"),
                    "parameters": {
                        "behavior_tags": liquid_class.get("behavior_tags", []),
                        "parameter_names": liquid_class.get("parameter_names", []),
                        "micro_command_types": liquid_class.get("micro_command_types", []),
                    },
                    "source_file": liquid_class.get("path"),
                }
            )
            summary[
                "liquid_classes_inserted" if inserted else "liquid_classes_updated"
            ] += 1
            self.add_global_value("liquid_classes", str(name))
            summary["global_values_added"] += 1

        for adapter in adapter_inventory:
            inserted = self.upsert_adapter(adapter)
            summary["adapters_inserted" if inserted else "adapters_updated"] += 1

        for category, key in (
            ("device_aliases", "device_aliases"),
            ("available_ids", "available_ids"),
            ("tool_ids", "tool_ids"),
        ):
            for value in device_inventory.get(key, []):
                if not value:
                    continue
                self.add_global_value(category, str(value))
                summary["global_values_added"] += 1

        for macro in macro_signatures:
            name = str(macro.get("name") or "").strip()
            if not name:
                continue
            module_name = str(macro.get("module_name") or "").strip()
            self.add_global_value(
                "macros",
                f"{module_name}:{name}" if module_name else name,
            )
            summary["global_values_added"] += 1

        return summary

    # =========================================================================
    # IMPORT/EXPORT
    # =========================================================================

    def import_from_yaml(self, yaml_path: Path):
        """Import commands from commands.yaml format."""
        import yaml

        with open(yaml_path) as f:
            data = yaml.safe_load(f)

        # Import commands
        for cmd in data.get("commands", []):
            self.upsert_command(cmd)

            # Import parameters
            for param in cmd.get("parameters", []):
                self.add_parameter(cmd["id"], param)

                # Import observed values
                for value in param.get("observed_values", []):
                    self.add_observed_value(cmd["id"], param["name"], str(value))

        # Import global values
        for category, values in data.get("observed_values", {}).items():
            for value in values:
                self.add_global_value(category, value)

    def export_to_yaml(self, yaml_path: Path):
        """Export to commands.yaml format for compatibility."""
        import yaml

        commands = []
        for cmd in self.get_all_commands():
            cmd_dict = {
                "id": cmd["id"],
                "type": cmd["type"],
                "category": cmd["category"],
                "description": cmd["description"],
                "template": cmd["template"],
            }

            # Get parameters
            with self._connection() as conn:
                params = conn.execute(
                    "SELECT * FROM parameters WHERE command_id = ?",
                    (cmd["id"],)
                ).fetchall()

                cmd_dict["parameters"] = []
                for param in params:
                    param_dict = {
                        "name": param["name"],
                        "type": param["type"],
                        "required": bool(param["required"]),
                    }

                    # Get observed values
                    values = conn.execute(
                        "SELECT value FROM observed_values WHERE parameter_id = ?",
                        (param["id"],)
                    ).fetchall()
                    if values:
                        param_dict["observed_values"] = [v["value"] for v in values]

                    cmd_dict["parameters"].append(param_dict)

            commands.append(cmd_dict)

        # Get global values
        observed_values = {}
        for category in ["labware_types", "locations", "liquid_classes", "device_aliases"]:
            values = self.get_global_values(category)
            if values:
                observed_values[category] = values

        data = {
            "commands": commands,
            "observed_values": observed_values,
            "metadata": {
                "exported_at": datetime.now().isoformat(),
                "total_commands": len(commands)
            }
        }

        with open(yaml_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_stats(self) -> Dict:
        """Get database statistics."""
        with self._connection() as conn:
            stats = {}
            stats["commands"] = conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
            stats["parameters"] = conn.execute("SELECT COUNT(*) FROM parameters").fetchone()[0]
            stats["observed_values"] = conn.execute("SELECT COUNT(*) FROM observed_values").fetchone()[0]
            stats["sequences"] = conn.execute("SELECT COUNT(*) FROM sequences").fetchone()[0]
            stats["patterns"] = conn.execute("SELECT COUNT(*) FROM patterns").fetchone()[0]
            stats["rules"] = conn.execute("SELECT COUNT(*) FROM rules WHERE active = 1").fetchone()[0]
            stats["modules"] = conn.execute("SELECT COUNT(*) FROM modules WHERE active = 1").fetchone()[0]
            stats["rule_evidence"] = conn.execute("SELECT COUNT(*) FROM rule_evidence").fetchone()[0]
            stats["labware"] = conn.execute("SELECT COUNT(*) FROM labware").fetchone()[0]
            stats["liquid_classes"] = conn.execute("SELECT COUNT(*) FROM liquid_classes").fetchone()[0]
            stats["worktable_positions"] = conn.execute("SELECT COUNT(*) FROM worktable_positions").fetchone()[0]
            stats["worktable_locations"] = conn.execute("SELECT COUNT(DISTINCT location) FROM worktable_positions").fetchone()[0]
            stats["dsl_recipes"] = conn.execute("SELECT COUNT(*) FROM dsl_recipes WHERE active = 1").fetchone()[0]

            # Category breakdown
            rows = conn.execute(
                "SELECT category, COUNT(*) as count FROM commands GROUP BY category"
            ).fetchall()
            stats["by_category"] = {row["category"]: row["count"] for row in rows}

            # Rules breakdown
            rows = conn.execute(
                "SELECT rule_type, COUNT(*) as count FROM rules WHERE active = 1 GROUP BY rule_type"
            ).fetchall()
            stats["rules_by_type"] = {row["rule_type"]: row["count"] for row in rows}

            return stats

    # =========================================================================
    # DSL RECIPES
    # =========================================================================

    def seed_dsl_recipes(self) -> int:
        """Seed curated DSL/API repair recipes idempotently."""
        from .dsl_recipes import seed_curated_dsl_recipes

        return seed_curated_dsl_recipes(self)

    def upsert_dsl_recipe(self, recipe: Dict[str, Any], embedder: Any = None) -> bool:
        """Insert or update a curated DSL recipe.

        Returns True if inserted, False if an existing row was updated.
        """
        from .dsl_recipes import HashingRecipeEmbedder

        now = datetime.now().isoformat()
        good_patterns = recipe.get("good_patterns") or []
        tags = recipe.get("tags") or []
        if not isinstance(good_patterns, list):
            good_patterns = list(good_patterns)
        if not isinstance(tags, list):
            tags = list(tags)
        embedder = embedder or HashingRecipeEmbedder()
        retrieval_text = " ".join(
            str(part)
            for part in (
                recipe.get("name"),
                recipe.get("object_key"),
                recipe.get("action"),
                recipe.get("failure_category") or "",
                recipe.get("bad_pattern") or "",
                " ".join(str(item) for item in good_patterns),
                recipe.get("context_text") or "",
                " ".join(str(item) for item in tags),
            )
            if part
        )
        embedding = recipe.get("embedding")
        if embedding is None:
            embedding = embedder.embed(retrieval_text)
        with self._connection() as conn:
            existing = conn.execute(
                "SELECT id FROM dsl_recipes WHERE name = ?",
                (recipe["name"],),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE dsl_recipes
                    SET object_key = ?, action = ?, failure_category = ?, bad_pattern = ?,
                        good_patterns = ?, context_text = ?, tags = ?, embedding = ?,
                        active = ?, updated_at = ?
                    WHERE name = ?
                    """,
                    (
                        recipe["object_key"],
                        recipe["action"],
                        recipe.get("failure_category"),
                        recipe.get("bad_pattern"),
                        json.dumps(good_patterns),
                        recipe.get("context_text"),
                        json.dumps(tags),
                        json.dumps(embedding),
                        1 if recipe.get("active", True) else 0,
                        now,
                        recipe["name"],
                    ),
                )
                return False
            conn.execute(
                """
                INSERT INTO dsl_recipes (
                    name, object_key, action, failure_category, bad_pattern,
                    good_patterns, context_text, tags, embedding, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recipe["name"],
                    recipe["object_key"],
                    recipe["action"],
                    recipe.get("failure_category"),
                    recipe.get("bad_pattern"),
                    json.dumps(good_patterns),
                    recipe.get("context_text"),
                    json.dumps(tags),
                    json.dumps(embedding),
                    1 if recipe.get("active", True) else 0,
                    now,
                    now,
                ),
            )
            return True

    def get_active_dsl_recipes(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM dsl_recipes WHERE active = 1 ORDER BY name"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_dsl_recipe(self, name: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM dsl_recipes WHERE name = ?",
                (name,),
            ).fetchone()
            return dict(row) if row else None

    def set_dsl_recipe_active(self, name: str, active: bool) -> bool:
        with self._connection() as conn:
            cursor = conn.execute(
                "UPDATE dsl_recipes SET active = ?, updated_at = ? WHERE name = ?",
                (1 if active else 0, datetime.now().isoformat(), name),
            )
            return cursor.rowcount > 0


    # =========================================================================
    # PROTOCOL PROCESSING (Extract sequences from .xscr files)
    # =========================================================================

    def extract_sequences_from_protocol(self, protocol_steps: List[str], source_file: str = None):
        """
        Extract command sequences from a protocol's step list.

        Args:
            protocol_steps: List of command IDs in order (e.g., ["AddLabware", "PickUpTips", "Aspirate", "Dispense"])
            source_file: Source file name for tracking
        """
        if len(protocol_steps) < 2:
            return

        # Record each adjacent pair
        for i in range(len(protocol_steps) - 1):
            from_cmd = protocol_steps[i]
            to_cmd = protocol_steps[i + 1]

            self.record_sequence(from_cmd, to_cmd, {"source": source_file})

    def extract_command_ids_from_xscr(self, xml_content: str) -> List[str]:
        """
        Extract ordered list of command IDs from an .xscr file.

        Parses the XML to find all Object types in <Statements> blocks,
        converting full type names to short IDs.
        """
        import re

        command_ids = []

        # Find all Object Type attributes in order
        # Pattern matches: <Object Type="Tecan.Core...CommandDataV1">
        pattern = re.compile(r'<Object\s+Type="([^"]+)"')

        for match in pattern.finditer(xml_content):
            full_type = match.group(1)

            # Convert to short ID (last part, remove DataV1/DataV2 suffix)
            short_id = full_type.split(".")[-1]
            short_id = re.sub(r'ScriptCommandDataV\d+$', '', short_id)
            short_id = re.sub(r'DataV\d+$', '', short_id)

            # Skip script group containers
            if short_id in ["ScriptGroup", "Script"]:
                continue

            command_ids.append(short_id)

        return command_ids

    def process_protocol_file(self, file_path: Path) -> Dict:
        """
        Process a single .xscr file to extract commands and sequences.

        Returns statistics about what was extracted.
        """
        content = file_path.read_text(encoding='utf-8-sig')

        # Extract command sequence
        command_ids = self.extract_command_ids_from_xscr(content)

        # Record sequences
        self.extract_sequences_from_protocol(command_ids, file_path.name)

        # Log the extraction
        with self._connection() as conn:
            conn.execute("""
                INSERT INTO extraction_log (file_name, extracted_at, sequences_found)
                VALUES (?, ?, ?)
            """, (file_path.name, datetime.now().isoformat(), len(command_ids) - 1))

        return {
            "file": file_path.name,
            "commands_found": len(command_ids),
            "sequences_recorded": max(0, len(command_ids) - 1),
            "command_sequence": command_ids
        }

    def detect_patterns(self, min_frequency: int = 3, min_length: int = 2, max_length: int = 5) -> List[Dict]:
        """
        Detect common patterns from recorded sequences.

        Uses n-gram analysis to find recurring command sequences.

        Args:
            min_frequency: Minimum times a pattern must occur
            min_length: Minimum pattern length
            max_length: Maximum pattern length

        Returns:
            List of detected patterns with frequency and confidence
        """
        # Get all sequences
        sequences = self.get_sequence_graph()

        if not sequences:
            return []

        # Build adjacency for path finding
        adjacency = {}
        for seq in sequences:
            from_cmd = seq["from_command_id"]
            to_cmd = seq["to_command_id"]
            freq = seq["frequency"]

            if from_cmd not in adjacency:
                adjacency[from_cmd] = []
            adjacency[from_cmd].append((to_cmd, freq))

        # Find common n-grams by walking the graph
        patterns = []

        # For each starting command, explore paths
        for start_cmd in adjacency:
            self._explore_patterns(
                adjacency, start_cmd, [start_cmd],
                patterns, min_frequency, min_length, max_length
            )

        # Deduplicate and rank by frequency
        pattern_counts = {}
        for pattern in patterns:
            key = tuple(pattern)
            if key not in pattern_counts:
                pattern_counts[key] = 0
            pattern_counts[key] += 1

        # Filter and format results
        results = []
        for pattern, count in sorted(pattern_counts.items(), key=lambda x: -x[1]):
            if count >= min_frequency and len(pattern) >= min_length:
                # Calculate confidence based on frequency
                confidence = min(1.0, count / 10.0)

                # Generate pattern name
                if len(pattern) == 2 and pattern[0] == "Aspirate" and pattern[1] == "Dispense":
                    name = "Transfer"
                elif "PickUpTips" in pattern and "SetTipsBack" in pattern:
                    name = "TipCycle"
                else:
                    name = "→".join(pattern[:3]) + ("..." if len(pattern) > 3 else "")

                results.append({
                    "name": name,
                    "steps": list(pattern),
                    "frequency": count,
                    "confidence": confidence
                })

                # Save to database
                self.save_pattern(
                    name=name,
                    steps=list(pattern),
                    description=f"Auto-detected pattern (freq={count})",
                    pattern_type="sequence"
                )

        return results

    def _explore_patterns(self, adjacency, current, path, patterns, min_freq, min_len, max_len):
        """Recursive helper for pattern detection."""
        if len(path) >= min_len:
            patterns.append(path.copy())

        if len(path) >= max_len:
            return

        if current not in adjacency:
            return

        for next_cmd, freq in adjacency[current]:
            if freq >= min_freq and next_cmd not in path:  # Avoid cycles
                path.append(next_cmd)
                self._explore_patterns(adjacency, next_cmd, path, patterns, min_freq, min_len, max_len)
                path.pop()

    def get_suggested_next_commands(self, current_commands: List[str], k: int = 3) -> List[Dict]:
        """
        Given current command sequence, suggest what comes next.

        Uses sequence statistics to predict likely next commands.

        Args:
            current_commands: Commands executed so far
            k: Number of suggestions to return

        Returns:
            List of suggested commands with confidence scores
        """
        if not current_commands:
            return []

        last_cmd = current_commands[-1]
        suggestions = self.get_next_commands(last_cmd, limit=k)

        # Boost suggestions that match known patterns
        patterns = self.find_patterns(min_frequency=2)
        for pattern in patterns:
            steps = pattern["steps"]
            # Check if current sequence is a prefix of this pattern
            if len(current_commands) < len(steps):
                if current_commands == steps[:len(current_commands)]:
                    next_in_pattern = steps[len(current_commands)]
                    # Boost this suggestion
                    for sug in suggestions:
                        if sug["command"] == next_in_pattern:
                            sug["frequency"] *= 2
                            sug["from_pattern"] = pattern["name"]

        return sorted(suggestions, key=lambda x: -x["frequency"])[:k]


# Global instance
_db: Optional[TecanDatabase] = None


def get_database() -> TecanDatabase:
    """Get or create global database instance."""
    global _db
    if _db is None:
        _db = TecanDatabase()
    return _db


def sync_install_bundle() -> Dict[str, int] | None:
    """Pull the current shared install bundle into the local DB."""
    from .fc_install import ingest_default_install_bundle

    return ingest_default_install_bundle(get_database())


def extract_sequences_from_file(file_path: Path) -> Dict:
    """Convenience function to extract sequences from a file."""
    db = get_database()
    return db.process_protocol_file(file_path)
