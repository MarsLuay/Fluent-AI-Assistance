## Declarative Verification Recipe (skip hand-built IR)

- For operator-verification / teaching scripts, put a `verification_recipe`
  block in `request.spec.yaml` instead of hand-editing the protocol IR. When the
  recipe declares groups, `build_ir_from_recipe` synthesizes the full IR
  (labware + ordered steps) automatically; no `--ir` file is needed.
- Populate every name from the imported full ZEIA / context (see **Lab / ZEIA
  name provenance**). The YAML below is a shape only.
- Shape:

  ```yaml
  verification_recipe:
    # Replace every <...> token with exact strings from the imported ZEIA.
    worktable: "<WorktableNameFromZeia>"       # optional override
    worktable_guid: "<WorktableGuidFromZeia>"  # optional
    labware:
      - {label: "<LabwareLabel>", catalog: "<CatalogExactFromZeia>", location: "<LocationFromZeia>", site: 1}
      - {label: "<PlateLabel>[platecount]", catalog: "<PlateCatalogFromZeia>", location: "<LocationFromZeia>", site: 3}
    simulation_values:
      # Only when the ZEIA/source script needs simulator-only expressions:
      - {name: '<ExpressionFromZeia>', value: 0}
    groups:
      - name: "Operator setup"
        description: "Confirms operator setup and deck load before verification moves."
        steps:
          - prompt: "Confirm external instruments are connected and initialized ..."
            instrument_init_check: true
          - prompt: "Confirm <LabwareLabel> is on the deck in the correct nest."
            deck_presence_check: true
            worktable_binding: <binding_key_for_labware>
          - prompt: "Confirm <PlateLabel> is on the deck in the correct position."
            deck_presence_check: true
            worktable_binding: <binding_key_for_plate>
      - name: "Arm verification"
        description: "Tests and confirms arm positioning."
        steps:
          - subroutine: "<ScriptsFolder>\\<SubroutineName>"
          - prompt: "Confirm gripper fingers are oriented correctly ..."
      - name: "Device / transfer verification"
        description: "Verifies a mined transfer from the source script."
        steps:
          - verified_move: {labware: "<LabwareLabel>", to_location: "<DestLocationFromZeia>", to_site: 1}
          - prompt: "Confirm the labware seated correctly after the move."
    worktable_patterns:            # optional; copy fields from mined source prompts
      example_load_pattern:
        labware: "<CarrierOrHolderLabel>"
        labware_type: "<LabwareTypeFromZeia>"
        grid: 1
        site: 3
  ```

- **RUP Worktable (deck only):** set `deck_presence_check: true` with
  `worktable_binding` on prompts that confirm an item is loaded on the deck
  *before* automated moves (initial placement). These compile to
  `RUPWorktableStatement` with deck labware highlight.
- **RUP Standard (external init + teaching):** set `instrument_init_check: true`
  on prompts that confirm external instruments mined from the ZEIA are connected
  and initialized — include reference media for power-button / init-screen
  walkthroughs. Post-move confirmations, arm checks, barcode/capping teaching
  prompts, and summary prompts also compile to `RUPStandardStatement` with
  `SelectedImagePath`. Do **not** set `deck_presence_check` or `worktable_binding`
  on non-deck prompts.
- Use `plain_prompt: true` only for brief text-only confirmations with no media
  (for example the run identity check at the start of Operator setup).
- **Deck load / presence checks:** use **one prompt per labware item**, each with
  `deck_presence_check: true` and its own `worktable_binding`.
  Do not combine multiple parts into a single deck-load prompt.
- Step types: `comment`, `prompt`, `subroutine` (string or
  `{name, execution_mode, variable_mappings_start, variable_mappings_end}`),
  `move`/`manual_move`
  (`{labware, to_location, to_site, onto}`), and `verified_move`. Each group may
  also declare `description:` — a short purpose comment emitted at the top of that
  group (for example `Tests and confirms arm positioning.`). Do not use `{comment: ...}`
  steps for script/worktable metadata; `validate-spec` warns and the builder drops
  those meta comments automatically. Each labware entry also emits an `add_labware`
  step in the single `Operator setup` group; if a recipe declares multiple
  setup-ish groups such as `Setup` and `Operator setup`, the workflow merges
  them into that one setup group. `move` and
  `manual_move` steps are flagged `force_manual_verification` and become
  manual-verification prompts via `convert_unsafe_rga_adapter_moves_to_prompts`.
  Use `verified_move` only when the user explicitly wants the physical movement
  to run first and a prompt to visually confirm it afterward; it emits an actual
  `move_plate` step tagged `allow_automated_verification_motion` and still
  requires final generation with `--approve-automated-motion` so the approval is
  recorded in the validation context.
  Use `simulation_values` for simulator-only Fluent runtime expressions mined
  from the ZEIA (expressions that cannot be declared as normal variables but
  must be seeded before subroutine simulation).
  Media placeholders and unsafe-move-to-prompt conversion still run automatically
  after recipe synthesis.

