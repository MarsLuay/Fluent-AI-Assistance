# Typed API V2 Command Parity Inventory

Source comparison:

- Typed command IDs: `fluent_pipeline.api_v2.commands.XSCR_COMMAND_ID_TO_API_V2`
- Registry commands: `source_command_registry_path()` via `fluent_pipeline.command_registry`

- Dedicated typed API V2 command families: `12`
- Typed compiled command IDs: `14`
- Mapped registry command IDs without typed parity: `22`
- Approved passthrough command IDs without typed parity: `14`

## Mapped Gaps

| Command ID | Operation | Family | FluentControl name |
| --- | --- | --- | --- |
| AlternateGroup | default_branch | Control flow | Else / Alternate |
| CommentStatement | comment | User/script flow | Comment |
| ConditionalGroup | conditional_branch | Control flow | If / Conditional |
| DropHeadAdapterScriptCommandDataV1 | drop_head_adapter | MCA384 | Drop Head Adapter |
| GetHeadAdapterScriptCommandDataV1 | get_head_adapter | MCA384 | Get Head Adapter |
| InitializeDeviceScriptCommandDataV1 | initialize_device | Device | Initialize |
| LihaAspirateScriptCommandDataV5 | liha_aspirate | LiHa/FCA | Aspirate |
| LihaDispenseScriptCommandDataV5 | liha_dispense | LiHa/FCA | Dispense |
| LihaDropTipsScriptCommandDataV5 | liha_drop_tips | LiHa/FCA | Drop Tips |
| LihaGetTipsScriptCommandDataV5 | liha_get_tips | LiHa/FCA | Get Tips |
| LihaMixScriptCommandDataV5 | liha_mix | LiHa/FCA | Mix |
| LihaWashScriptCommandDataV1 | wash | LiHa/FCA | Wash Tips |
| LoopGroupDataV1 | loop_over_wells | Control flow | Loop |
| Mca384AspirateScriptCommandDataV2 | aspirate | MCA384 | Aspirate |
| Mca384DispenseScriptCommandDataV2 | dispense | MCA384 | Dispense |
| Mca384DropTipsScriptCommandDataV5 | mca384_drop_tips | MCA384 | Drop Tips |
| Mca384MixScriptCommandDataV2 | mca384_mix | MCA384 | Mix |
| Mca384PickUpTipsScriptCommandDataV5 | pick_up_tips | MCA384 | Get Tips |
| Mca384SetTipsBackScriptCommandDataV5 | set_tips_back | MCA384 | Set Tips Back |
| Mca384WashScriptCommandDataV1 | wash | MCA384 | Wash Tips |
| MovePlateScriptCommandDataV1 | move_plate | RGA/CGA | Move Labware (Transfer Labware) |
| ReadWorklistScriptCommandDataV1 | read_worklist | Worklist | Read Worklist |

## Approved Passthrough Without Dedicated Model

These are intentionally accepted through `GenericCommand`/approved raw XML today.

| Command ID | Family | FluentControl name |
| --- | --- | --- |
| CgaDropFingersScriptCommand | RGA/CGA | Drop Fingers |
| ExecuteApplicationStatement | External application | Execute Application |
| ExecuteVbScriptStatement | External script | Execute VBScript |
| GenerateReportStatement | Reporting | Generate Report |
| InteriorLightOffStatement | Device | Interior Light Off |
| InteriorLightOnStatement | Device | Interior Light On |
| LeaveStatement | Control flow | Leave |
| LegacyDriverMacro | Legacy driver | Run legacy driver macro |
| LihaDetectLiquidScriptCommand | LiHa/FCA | Detect Liquid |
| MoveAxisCommandScriptStatement | Application driver | Move Axis |
| RaiseErrorStatement | User/script flow | Raise Error |
| StartMoveCommandScriptStatement | Application driver | Start Move |
| TeGioSetPWMOutputStatement | Application driver | Set PWM Output |
| WaitForAsyncResponseScriptStatement | Application driver | Wait For Async Response |

## Regenerate

```powershell
.\.venv\Scripts\python.exe -m tools.inventory_api_v2_command_parity --write-doc
```
