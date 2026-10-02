# Optional texture reimport coordination

The existing manual texture reimport stays available. For an owner-admitted heavy
`unreal:MyProject2` phase, use:

```python
bpy.ops.ue_unique_names.reimport_unreal_textures(workstation_phase_id=phase_id)
```

This uses the installed Send2UE `coordination` module and Workstation Queue's
`pipeline_bridge.py`. `WORKSTATION_QUEUE_REPO` can override that bridge's repository.
The phase must hold exclusive `editor`: source-matching texture settings can change
outside the sidecar mesh folder. The exact native request is bound to the refreshed
JSON path. Completion requires the remote command receipt and a native result for
every requested unique texture entry. A partial import, exception or timeout retains
the phase's recovery reservation.

`sync_painter_low_export` and automatic `Baking/low` collection links remain local
Blender synchronization. Their receipts do not complete Painter or Unreal phases.

Run `python -m unittest discover -s tests -p test_native_coordination.py -v` for
isolated fake-module tests without launching Blender or Unreal.
