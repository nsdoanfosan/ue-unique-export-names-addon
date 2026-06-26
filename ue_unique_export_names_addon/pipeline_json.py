import json
from pathlib import Path

import bpy

from .gpro import (
    effective_material_slot_entries,
    has_gpro_instance_material_source,
    is_unreal_handoff_material,
    material_usage_lookup,
    material_usage_text,
)
from .transfer import transfer_postprocess_entry
from .unreal_material_json import _material_json_entry
from .utils import clean_token, export_collection
from .validation import export_validation_rows


def _append_unique_name(target, seen, name):
    name = str(name or "")
    key = name.casefold()
    if name and key not in seen:
        seen.add(key)
        target.append(name)


def _material_instance_base_name(material_name):
    if material_name.startswith("M_"):
        return material_name[2:]
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
        "schema_version": 2,
        "material_pipeline": "surface_layers",
        "material_master": "prop",
        "mesh_name": mesh_name,
        "asset_prefix": prefix,
        "materials": material_entries,
        "cleanup": _cleanup_json_entry(material_entries),
    }
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
):
    json_dir.mkdir(parents=True, exist_ok=True)
    json_paths = []
    object_names = {clean_token(obj.name) for obj in objects}
    validation_rows = export_validation_rows(
        context,
        objects=objects,
        materials=materials,
        texture_map=texture_map,
    )
    validation_by_object_name = {
        row["object_name"]: row
        for row in validation_rows
    }

    if not combined_only:
        # Per standalone mesh-object sidecar. Child meshes under an Empty export
        # as the Empty/group asset, so their contract lives in the Empty JSON.
        for obj in objects:
            if obj.parent and obj.parent.type == "EMPTY":
                continue
            mesh_name = clean_token(obj.name)
            entries = []
            seen_materials = set()
            for slot_index, mat, _location in effective_material_slot_entries(obj):
                if (
                    not mat
                    or not is_unreal_handoff_material(mat)
                    or mat in seen_materials
                ):
                    continue
                seen_materials.add(mat)
                entries.append(_material_json_entry(mat, slot_index, texture_map))
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

    # 2) Per EMPTY-parent sidecar. Send to Unreal "Combine > Child meshes" merges an empty's child
    #    meshes into ONE asset named after the empty (combine_assets.py pre_mesh_export), so without
    #    this the combined mesh has no matching <name>.json and its materials break. We aggregate all
    #    child materials (unique, first-encounter order). The Unreal side matches slots by material
    #    name, so slot_index here is only a best-effort hint for the combined slot order.
    children_by_empty = {}
    empties_in_order = []
    for obj in objects:
        parent = obj.parent
        if parent and parent.type == "EMPTY":
            if parent.name not in children_by_empty:
                children_by_empty[parent.name] = []
                empties_in_order.append(parent)
            children_by_empty[parent.name].append(obj)

    for empty in empties_in_order:
        empty_name = clean_token(empty.name)
        # If a mesh object already owns this name, its per-object sidecar wins — don't clobber it.
        if empty_name in object_names:
            continue
        entries = []
        seen_materials = set()
        slot_index = 0
        for obj in children_by_empty[empty.name]:
            for _source_slot_index, mat, _location in effective_material_slot_entries(obj):
                if (
                    not mat
                    or not is_unreal_handoff_material(mat)
                    or mat in seen_materials
                ):
                    continue
                seen_materials.add(mat)
                entries.append(_material_json_entry(mat, slot_index, texture_map))
                slot_index += 1
        if entries:
            child_validation = [
                validation_by_object_name[obj.name]
                for obj in children_by_empty[empty.name]
                if obj.name in validation_by_object_name
            ]
            json_paths.append(
                _write_pipeline_sidecar(
                    json_dir,
                    empty_name,
                    prefix,
                    entries,
                    validation_children=child_validation,
                    transfer_sources=[
                        transfer_postprocess_entry(obj)
                        for obj in children_by_empty[empty.name]
                    ],
                )
            )

    if json_paths:
        cleanup_stale_pipeline_sidecars(json_dir, objects, json_paths)
        context.scene.ue_unique_names.last_pipeline_json_path = str(json_paths[-1])
    return json_paths


def cleanup_stale_pipeline_sidecars(json_dir, objects, keep_paths):
    keep = {Path(path).resolve() for path in keep_paths}
    candidate_names = {clean_token(obj.name) for obj in objects}
    for obj in objects:
        parent = obj.parent
        if parent and parent.type == "EMPTY":
            candidate_names.add(clean_token(parent.name))
    for name in candidate_names:
        path = (json_dir / f"{name}.json").resolve()
        if path in keep or not path.exists():
            continue
        try:
            path.unlink()
        except OSError:
            pass


def _json_target_names(objects, combined_only=False):
    names = []
    if not combined_only:
        names.extend(
            obj.name
            for obj in objects
            if not (obj.parent and obj.parent.type == "EMPTY")
        )

    object_names = {clean_token(obj.name) for obj in objects}
    children_by_empty = {}
    empties_in_order = []
    for obj in objects:
        parent = obj.parent
        if parent and parent.type == "EMPTY":
            if parent.name not in children_by_empty:
                children_by_empty[parent.name] = []
                empties_in_order.append(parent)
            children_by_empty[parent.name].append(obj)

    for empty in empties_in_order:
        if clean_token(empty.name) in object_names:
            continue
        names.append(empty.name)
    return names


def _validate_clean_name(label, name, errors):
    clean = clean_token(name)
    if clean != name:
        errors.append(f"{label} '{name}' would be written as '{clean}'. Rename it explicitly first.")


def _json_refresh_validation_errors(context, props, objects, materials, texture_map):
    errors = []
    material_usage = material_usage_lookup(objects)
    if props.scope == "EXPORT_COLLECTION" and export_collection(context) is None:
        errors.append("Export collection does not exist.")
    if not objects:
        errors.append("No export objects in the selected JSON scope.")
        return errors
    if not materials:
        errors.append("No materials found in the selected JSON scope.")

    target_names = _json_target_names(objects)
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
            for slot_index, mat, _location in effective_material_slot_entries(obj)
            if mat and is_unreal_handoff_material(mat)
        ]
        effective_slots = effective_material_slot_entries(obj)
        if not effective_slots:
            errors.append(f"Mesh '{obj.name}' has no material slots.")
            continue
        if not handoff_slots:
            continue
        for slot_index, mat, _location in effective_slots:
            if mat is None and not has_gpro_instance_material_source(obj):
                errors.append(f"Mesh '{obj.name}' slot {slot_index} has no material.")

    for material in materials:
        usage = material_usage_text(material, material_usage)
        _validate_clean_name("Material", material.name, errors)
        if not clean_token(material.name).startswith("M_"):
            errors.append(
                f"Material '{material.name}' must use the M_ prefix. Used by: {usage}."
            )

        textures = texture_map.get(material, {})
        if not textures:
            # Texture-less handoff materials are valid: Unreal can still create
            # and assign a material instance, leaving texture parameters empty.
            continue

        for role, image in textures.items():
            source_value = image.filepath_raw or image.filepath
            if not source_value:
                errors.append(
                    f"Texture '{image.name}' ({role}) has no file path. "
                    f"Material: {material.name}. Used by: {usage}."
                )
                continue
            source_path = Path(bpy.path.abspath(source_value))
            if not source_path.is_file():
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
