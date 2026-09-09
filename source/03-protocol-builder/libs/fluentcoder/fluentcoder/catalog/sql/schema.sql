
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
