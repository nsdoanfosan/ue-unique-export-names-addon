import json
from pathlib import Path
import sys

import bpy


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from ue_unique_export_names_addon import unreal_material_json


material = bpy.data.materials.new("M_HT_Default_Material_01")
material.use_nodes = True
contract = {
    "schema": "htue.material.v3",
    "version": 3,
    "material_instance_path": "/Game/Material/HairTool/MI/MI_HT_Default_Material_01",
    "create_if_missing": False,
    "manage_existing_material_instance": True,
    "material_instance_ownership": "pipeline",
    "textures": [
        {
            "param": "IRD Map",
            "asset_name": "Hair_Long_01_IRD",
            "file": "D:/Hair_Long_01_IRD.tga",
            "virtual_texture_streaming": True,
        }
    ],
    "hair_tool": {
        "contract_version": 3,
        "control_source_material": material.name,
        "sync_parameters": ["System Color Influence", "System Blend Mode"],
        "vector_parameters": {},
        "vertex_uv_payload": {
            "version": 3,
            "encoding": "HTUE_RGB_TAGGED_UV",
            "system_color_alpha_used": False,
        },
        "scalar_parameters": {
            "System Color Influence": 1.0,
            "System Blend Mode": 2.0,
        },
    },
}
material["htue_contract_json"] = json.dumps(contract)

entry = unreal_material_json._material_json_entry(material, 3, {})
assert entry["master_preset"] == "hair"
assert entry["material_instance_path"] == contract["material_instance_path"]
assert entry["create_if_missing"] is False
assert entry["manage_existing_material_instance"] is True
assert entry["material_instance_ownership"] == "pipeline"
assert entry["textures"] == contract["textures"]
assert entry["hair_tool"] == contract["hair_tool"]

print("HTUE_EXPORT_CONTRACT_SMOKE_OK")
