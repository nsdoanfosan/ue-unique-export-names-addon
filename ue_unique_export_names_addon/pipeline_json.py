import json
from pathlib import Path

from .constants import MATERIAL_PREFIX
from .contract import speedtree_handoff_contract
from .gpro import (
    effective_material_slot_entries,
    has_gpro_instance_material_source,
    material_usage_lookup,
    material_usage_text,
    unreal_handoff_material_slot_entries,
)
from .naming import (
    TEXTURE_PATH_MISSING,
    TEXTURE_PATH_NO_PATH,
    datablock_library_name,
    image_texture_path_issue,
    top_empty_parent,
)
from .transfer import transfer_postprocess_entry
from .unreal_material_json import (
    _material_json_entry,
    _speedtree_material_intent,
    _unreal_instance_profile,
    is_translucent_material,
    master_preset_for_material,
)
from .utils import clean_token, export_collection
from .validation import export_validation_rows
from .validation import _hair_asset_validation_row


def _asset_unit_groups(context, objects):
    """Group mesh objects by the asset root Send to Unreal will name.

    Send to Unreal can use the highest Empty in the Export collection as the
    asset name even when an Armature (or another in-scope object) sits between
    that Empty and the mesh.  Direct-parent-only grouping therefore produces a
    sidecar for the child mesh name instead of the imported asset name.
    """
    objects = list(objects)
    export_coll = export_collection(context)
    scope_objects = set(export_coll.all_objects) if export_coll else set()

    # Selected/scene workflows can contain objects outside the Export
    # collection.  Their own parent chains are still a valid naming scope.
    for obj in objects:
        if obj in scope_objects:
            continue
        scope_objects.add(obj)
        parent = obj.parent
        while parent is not None:
            scope_objects.add(parent)
            parent = parent.parent

    standalone = []
    groups_by_root = {}
    roots_in_order = []
    for obj in objects:
        root = top_empty_parent(obj, scope_objects)
        if root is None:
            standalone.append(obj)
            continue
        if root not in groups_by_root:
            groups_by_root[root] = []
            roots_in_order.append(root)
        groups_by_root[root].append(obj)
    return standalone, [(root, groups_by_root[root]) for root in roots_in_order]


def _append_unique_name(target, seen, name):
    name = str(name or "")
    key = name.casefold()
    if name and key not in seen:
        seen.add(key)
        target.append(name)


def _material_instance_base_name(material_name):
    if material_name.startswith(MATERIAL_PREFIX):
        return material_name[len(MATERIAL_PREFIX):]
    if material_name.startswith("MI_"):
        return material_name[3:]
    return material_name


def _add_cleanup_material_names(target, seen, material_name):
    _append_unique_name(target, seen, material_name)
    base_name = _material_instance_base_name(str(material_name or ""))
    _append_unique_name(target, seen, base_name)
    for prefix in ("LayerBlend_", "Prop_", "Coat_"):
        if base_name.startswith(prefix):
            _append_unique_name(target, seen, base_name[len(prefix):])


def _add_cleanup_texture_name(target, seen, texture):
    file_path = str(texture.get("file", ""))
    if file_path:
        _append_unique_name(target, seen, Path(file_path).stem)
        return
    _append_unique_name(target, seen, texture.get("asset_name", ""))


def _cleanup_json_entry(material_entries):
    material_names = []
    texture_names = []
    seen_material_names = set()
    seen_texture_names = set()

    for entry in material_entries:
        _add_cleanup_material_names(material_names, seen_material_names, entry.get("name", ""))
        _add_cleanup_material_names(material_names, seen_material_names, entry.get("slot_name", ""))
        for texture in entry.get("textures", []):
            _add_cleanup_texture_name(texture_names, seen_texture_names, texture)
        for layer in entry.get("layers", []):
            for texture in layer.get("textures", []):
                _add_cleanup_texture_name(texture_names, seen_texture_names, texture)

    return {
        "source_material_names": material_names,
        "source_texture_names": texture_names,
    }


def _material_master_for_entries(material_entries):
    if material_entries and all(
        entry.get("master_preset") == "tree"
        for entry in material_entries
    ):
        return "tree"
    return "prop"


def _speedtree_sidecar_descriptor(material_entries, mesh_name):
    if not any(
        entry.get("master_preset") == "tree"
        for entry in material_entries
        if isinstance(entry, dict)
    ):
        return None
    contract_api = speedtree_handoff_contract()
    if contract_api is None:
        return None
    return contract_api.build_sidecar_descriptor(mesh_name)


def _speedtree_dynamic_wind_path(json_dir, mesh_name, descriptor):
    if descriptor is None:
        return None
    contract_api = speedtree_handoff_contract()
    if contract_api is None:
        return None
    wind_rules = contract_api.dynamic_wind_rules()
    suffix = str(wind_rules.get("filename_suffix") or "")
    if not suffix:
        return None
    wind_path = Path(json_dir) / f"{mesh_name}{suffix}"
    if not wind_path.is_file():
        return None
    return wind_path.resolve().as_posix()


def _write_pipeline_sidecar(
    json_dir,
    mesh_name,
    prefix,
    material_entries,
    validation=None,
    validation_children=None,
    transfer_source=None,
    transfer_sources=None,
):
    data = {
        "schema_version": 3,
        "material_pipeline": "surface_layers",
        "material_master": _material_master_for_entries(material_entries),
        "mesh_name": mesh_name,
        "asset_prefix": prefix,
        "materials": material_entries,
        "cleanup": _cleanup_json_entry(material_entries),
    }
    speedtree_descriptor = _speedtree_sidecar_descriptor(
        material_entries,
        mesh_name,
    )
    if speedtree_descriptor is not None:
        data["speedtree_handoff_contract"] = speedtree_descriptor
    dynamic_wind_path = _speedtree_dynamic_wind_path(
        json_dir,
        mesh_name,
        speedtree_descriptor,
    )
    if dynamic_wind_path is not None:
        data["dynamic_wind_json"] = dynamic_wind_path
    if validation is not None:
        data["validation"] = validation
    if validation_children:
        data["validation_children"] = validation_children
    if transfer_source is not None:
        data["transfer_source"] = transfer_source
    if transfer_sources:
        data["transfer_sources"] = transfer_sources
    json_path = json_dir / f"{mesh_name}.json"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return json_path


def write_unreal_pipeline_json(
    context,
    prefix,
    objects,
    materials,
    texture_map,
    json_dir,
    combined_only=False,
    hair_assets=None,
):
    hair_assets = list(hair_assets or [])
    json_dir.mkdir(parents=True, exist_ok=True)
    json_paths = []
    validation_rows = export_validation_rows(
        context,
        objects=objects,
        materials=materials,
        texture_map=texture_map,
        hair_assets=hair_assets,
    )
    validation_by_object_name = {
        row["object_name"]: row
        for row in validation_rows
    }
    validation_by_asset_unit = {
        asset["asset_name"]: _hair_asset_validation_row(asset, texture_map)
        for asset in hair_assets
    }
    hair_assets_by_name = {
        clean_token(asset["asset_name"]): asset
        for asset in hair_assets
    }
    written_target_names = set()
    standalone_objects, asset_unit_groups = _asset_unit_groups(context, objects)

    if not combined_only:
        # Meshes belonging to an Empty asset unit use the Empty JSON even when
        # an Armature or another in-scope object sits between them.
        for obj in standalone_objects:
            mesh_name = clean_token(obj.name)
            entries = []
            seen_materials = set()
            for slot_index, mat, _location in unreal_handoff_material_slot_entries(obj):
                if (
                    not mat
                    or mat in seen_materials
                ):
                    continue
                seen_materials.add(mat)
                entry = _material_json_entry(mat, slot_index, texture_map)
                if has_gpro_instance_material_source(obj):
                    entry["slot_match_required"] = True
                entries.append(entry)
            json_paths.append(
                _write_pipeline_sidecar(
                    json_dir,
                    mesh_name,
                    prefix,
                    entries,
                    validation=validation_by_object_name.get(obj.name),
                    transfer_source=transfer_postprocess_entry(obj),
                )
            )
            written_target_names.add(mesh_name)

    # 2) Per Empty asset-unit sidecar. Send to Unreal names/combines the unit by
    # its highest Empty, so aggregate all descendant mesh materials in order.
    standalone_names = {clean_token(obj.name) for obj in standalone_objects}
    for empty, unit_objects in asset_unit_groups:
        empty_name = clean_token(empty.name)
        # A genuinely standalone mesh with this name already owns the sidecar.
        if empty_name in standalone_names:
            continue
        entries = []
        seen_materials = set()
        slot_index = 0
        for obj in unit_objects:
            for _source_slot_index, mat, _location in unreal_handoff_material_slot_entries(obj):
                if (
                    not mat
                    or mat in seen_materials
                ):
                    continue
                seen_materials.add(mat)
                entry = _material_json_entry(mat, slot_index, texture_map)
                if has_gpro_instance_material_source(obj):
                    entry["slot_match_required"] = True
                entries.append(entry)
                slot_index += 1
        if entries:
            child_validation = [
                validation_by_object_name[obj.name]
                for obj in unit_objects
                if obj.name in validation_by_object_name
            ]
            hair_asset = hair_assets_by_name.get(empty_name)
            if hair_asset:
                for material in hair_asset["materials"]:
                    if material in seen_materials:
                        continue
                    seen_materials.add(material)
                    entries.append(_material_json_entry(material, slot_index, texture_map))
                    slot_index += 1
                hair_validation = validation_by_asset_unit.get(hair_asset["asset_name"])
                if hair_validation:
                    child_validation.append(hair_validation)
            json_paths.append(
                _write_pipeline_sidecar(
                    json_dir,
                    empty_name,
                    prefix,
                    entries,
                    validation_children=child_validation,
                    transfer_sources=[
                        transfer_postprocess_entry(obj)
                        for obj in unit_objects
                    ],
                )
            )
            written_target_names.add(empty_name)

    if not combined_only:
        for asset in hair_assets:
            mesh_name = clean_token(asset["asset_name"])
            if mesh_name in written_target_names:
                continue
            entries = [
                _material_json_entry(material, slot_index, texture_map)
                for slot_index, material in enumerate(asset["materials"])
            ]
            json_paths.append(
                _write_pipeline_sidecar(
                    json_dir,
                    mesh_name,
                    prefix,
                    entries,
                    validation=validation_by_asset_unit.get(asset["asset_name"]),
                )
            )
            written_target_names.add(mesh_name)

    if json_paths:
        cleanup_stale_pipeline_sidecars(
            context,
            json_dir,
            objects,
            json_paths,
            hair_assets=hair_assets,
        )
        context.scene.ue_unique_names.last_pipeline_json_path = str(json_paths[-1])
    return json_paths


def cleanup_stale_pipeline_sidecars(context, json_dir, objects, keep_paths, hair_assets=None):
    hair_assets = list(hair_assets or [])
    keep = {Path(path).resolve() for path in keep_paths}
    candidate_names = {clean_token(obj.name) for obj in objects}
    candidate_names.update(clean_token(asset["asset_name"]) for asset in hair_assets)
    _standalone, asset_unit_groups = _asset_unit_groups(context, objects)
    candidate_names.update(
        clean_token(root.name)
        for root, _unit_objects in asset_unit_groups
    )
    for name in candidate_names:
        path = (json_dir / f"{name}.json").resolve()
        if path in keep or not path.exists():
            continue
        try:
            path.unlink()
        except OSError:
            pass


def _json_target_names(objects, combined_only=False, hair_assets=None, context=None):
    hair_assets = list(hair_assets or [])
    names = []
    standalone_objects, asset_unit_groups = _asset_unit_groups(context, objects)
    if not combined_only:
        names.extend(obj.name for obj in standalone_objects)

    standalone_names = {clean_token(obj.name) for obj in standalone_objects}
    for empty, _unit_objects in asset_unit_groups:
        if clean_token(empty.name) in standalone_names:
            continue
        names.append(empty.name)
    existing_names = {clean_token(name) for name in names}
    if not combined_only:
        for asset in hair_assets:
            clean_name = clean_token(asset["asset_name"])
            if clean_name in existing_names:
                continue
            names.append(asset["asset_name"])
            existing_names.add(clean_name)
    return names


def _validate_clean_name(label, name, errors, datablock=None):
    clean = clean_token(name)
    if clean == name:
        return
    library = datablock_library_name(datablock) if datablock is not None else ''
    if library:
        # The rename the user is being asked for is impossible in this blend, so
        # point at the file where it is possible instead.
        errors.append(
            f"{label} '{name}' is linked from '{library}' and cannot be renamed "
            f"here. Rename it to '{clean}' in that library, or make it local."
        )
        return
    errors.append(f"{label} '{name}' would be written as '{clean}'. Rename it explicitly first.")


def _json_refresh_validation_errors(context, props, objects, materials, texture_map, hair_assets=None):
    hair_assets = list(hair_assets or [])
    errors = []
    material_usage = material_usage_lookup(objects)
    for asset in hair_assets:
        for material in asset["materials"]:
            material_usage.setdefault(material, []).append(
                f"{asset['asset_name']} Hair Tool profile"
            )
    if props.scope == "EXPORT_COLLECTION" and export_collection(context) is None:
        errors.append("Export collection does not exist.")
    if not objects and not hair_assets:
        errors.append("No export objects in the selected JSON scope.")
        return errors
    if not materials:
        errors.append("No materials found in the selected JSON scope.")

    target_names = _json_target_names(
        objects,
        hair_assets=hair_assets,
        context=context,
    )
    for name in target_names:
        _validate_clean_name("JSON target", name, errors)
    duplicated_targets = sorted(
        name for name in set(target_names) if target_names.count(name) > 1
    )
    if duplicated_targets:
        errors.append("Duplicate JSON target names: " + ", ".join(duplicated_targets))

    for obj in objects:
        handoff_slots = [
            (slot_index, mat)
            for slot_index, mat, _location in unreal_handoff_material_slot_entries(obj)
        ]
        effective_slots = effective_material_slot_entries(obj)
        if not effective_slots:
            errors.append(f"Mesh '{obj.name}' has no material slots.")
            continue
        if not handoff_slots:
            continue
        for slot_index, mat, _location in effective_slots:
            if mat is None:
                errors.append(f"Mesh '{obj.name}' slot {slot_index} has no material.")

    for material in materials:
        usage = material_usage_text(material, material_usage)
        _validate_clean_name("Material", material.name, errors, material)
        if not clean_token(material.name).startswith(MATERIAL_PREFIX):
            library = datablock_library_name(material)
            if library:
                errors.append(
                    f"Material '{material.name}' is linked from '{library}' and "
                    f"must use the {MATERIAL_PREFIX} prefix. Rename it to "
                    f"'{MATERIAL_PREFIX}{clean_token(material.name)}' in that "
                    f"library, or make it local. Used by: {usage}."
                )
            else:
                errors.append(
                    f"Material '{material.name}' must use the {MATERIAL_PREFIX} prefix. Used by: {usage}."
                )

        try:
            if master_preset_for_material(material) == "tree":
                instance_profile = _unreal_instance_profile(material)
                _speedtree_material_intent(
                    material,
                    instance_profile=instance_profile,
                )
                if instance_profile and is_translucent_material(material):
                    errors.append(
                        f"Tree material '{material.name}' cannot combine a "
                        "translucent handoff with unreal_instance_profile. "
                        f"Used by: {usage}."
                    )
        except ValueError as exc:
            errors.append(
                f"Invalid SpeedTree handoff for material '{material.name}': "
                f"{exc}. Used by: {usage}."
            )

        textures = texture_map.get(material, {})
        if not textures:
            # Texture-less handoff materials are valid: Unreal can still create
            # and assign a material instance, leaving texture parameters empty.
            continue

        for role, image in textures.items():
            issue, source_path = image_texture_path_issue(image)
            if issue == TEXTURE_PATH_NO_PATH:
                errors.append(
                    f"Texture '{image.name}' ({role}) has no file path. "
                    f"Material: {material.name}. Used by: {usage}."
                )
            elif issue == TEXTURE_PATH_MISSING:
                errors.append(
                    f"Missing texture file: {image.name} ({role}). "
                    f"Material: {material.name}. Used by: {usage}. Path: {source_path}"
                )
    return errors


def _report_validation_errors(operator, errors):
    for error in errors:
        print(f"[Unreal Handoff Validator] Unreal handoff validation: {error}")
    first = errors[0] if errors else "Unknown validation error."
    if len(errors) == 1:
        operator.report({"ERROR"}, f"Unreal handoff blocked: {first}")
    else:
        operator.report(
            {"ERROR"},
            f"Unreal handoff blocked: {len(errors)} issues. First: {first}",
        )
