"""Native packed texture lifecycle regression; writes only a temporary folder.

Run with Blender --background --factory-startup --python-exit-code 1 --python
this_file.py. Registration does not change persistent user preferences.
"""
import hashlib
from pathlib import Path
import sys
import tempfile

import addon_utils
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ue_unique_export_names_addon import naming


def payload(image):
    return bytes(image.packed_file.data) if image.packed_file else None


def pixels(image):
    return tuple(image.pixels[:])


def assert_pixels(image, expected):
    actual = pixels(image)
    assert len(actual) == len(expected)
    assert max(abs(a - b) for a, b in zip(actual, expected)) < 1e-6, (actual, expected)


def fixture(directory, name, packed=True):
    created = bpy.data.images.new(name + '_Writer', width=4, height=4)
    created.pixels[:] = [1.0, 0.0, 0.0, 1.0] * 16
    created.filepath_raw = str(directory / (name + ('.jpg' if packed else '.png')))
    created.file_format = 'JPEG' if packed else 'PNG'
    created.save()
    path = created.filepath_raw
    bpy.data.images.remove(created)
    image = bpy.data.images.load(path, check_existing=False)
    image.name = name
    image.use_fake_user = True
    if packed:
        image.pack()
        image.filepath = ''
        image.filepath_raw = ''
    return image


def setup_material(image):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    material = bpy.data.materials.new('PackedRestoreMaterial')
    material.use_nodes = True
    node = material.node_tree.nodes.new('ShaderNodeTexImage')
    node.image = image
    shader = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')
    material.node_tree.links.new(node.outputs['Color'], shader.inputs['Base Color'])
    mesh = bpy.data.meshes.new('PackedRestoreMesh')
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.materials.append(material)
    obj = bpy.data.objects.new('PackedRestoreObject', mesh)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)


def save_reload(path):
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    bpy.ops.wm.open_mainfile(filepath=str(path))


def prepare_props(directory, prefix):
    props = bpy.context.scene.ue_unique_names
    props.scope = 'SELECTED'
    props.prefix_mode = 'CUSTOM'
    props.custom_prefix = prefix
    props.texture_handling = 'WRITE_FILES'
    props.texture_export_dir = str(directory / 'textures')
    props.write_manifest = False


with tempfile.TemporaryDirectory(prefix='ueun_packed_restore_') as tmp:
    directory = Path(tmp)
    assert addon_utils.enable('ue_unique_export_names_addon', default_set=False)

    for dirty in (False, True):
        name = 'DirtyPacked' if dirty else 'CleanPacked'
        image = fixture(directory, name)
        original = payload(image)
        original_format = image.file_format
        setup_material(image)
        # Exercise the packed but not yet decoded state of GLB imports.
        save_reload(directory / (name + '_input.blend'))
        image = bpy.data.images[name]
        original_format = image.file_format
        if dirty:
            image.pixels[:] = [0.0, 1.0, 0.0, 1.0] * 16
            image.update()
            assert image.is_dirty and payload(image) == original
        expected_pixels = pixels(image)
        prepare_props(directory, name)
        assert bpy.ops.ue_unique_names.prepare('EXEC_DEFAULT') == {'FINISHED'}
        prepared_name = image.name
        target = Path(image.filepath_raw)
        assert target.read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
        assert image.packed_file is None
        assert_pixels(image, expected_pixels)
        backup = image[naming.PACKED_RESTORE_BACKUP_PROP]
        assert payload(backup) == original
        first_target = target.read_bytes()
        first_backup = backup.as_pointer()
        assert bpy.ops.ue_unique_names.prepare('EXEC_DEFAULT') == {'FINISHED'}
        assert image.name == prepared_name and Path(image.filepath_raw) == target
        assert target.read_bytes() == first_target
        assert image[naming.PACKED_RESTORE_BACKUP_PROP].as_pointer() == first_backup

        # The recovery data must survive closing the prepared .blend, not only
        # an uninterrupted process with an in-memory cache.
        save_reload(directory / (name + '_prepared.blend'))
        image = bpy.data.images[prepared_name]
        assert payload(image[naming.PACKED_RESTORE_BACKUP_PROP]) == original
        assert bpy.ops.ue_unique_names.restore('EXEC_DEFAULT') == {'FINISHED'}
        image = bpy.data.images[name]
        assert image.filepath == '' and image.filepath_raw == ''
        if not dirty:
            assert image.file_format == original_format
        assert naming.PACKED_RESTORE_BACKUP_PROP not in image
        assert_pixels(image, expected_pixels)
        if dirty:
            recovery = image[naming.PACKED_ORIGINAL_RECOVERY_PROP]
            assert payload(recovery) == original
        else:
            assert payload(image) == original
        save_reload(directory / (name + '_restored.blend'))
        image = bpy.data.images[name]
        if not dirty:
            assert image.file_format == original_format
        assert_pixels(image, expected_pixels)
        if dirty:
            assert payload(image[naming.PACKED_ORIGINAL_RECOVERY_PROP]) == original
        else:
            assert payload(image) == original
        print(name, 'roundtrip OK', hashlib.sha256(original).hexdigest())

    # A failure after the file was written and the source unpacked must put
    # both exact original bytes and any live dirty pixels back.
    for dirty in (False, True):
        image = fixture(directory, 'WriteFailure' + str(dirty))
        original = payload(image)
        if dirty:
            image.pixels[:] = [0.0, 0.0, 1.0, 1.0] * 16
            image.update()
        expected_pixels = pixels(image)
        original_writer = naming._write_packed_image_as_png

        def fail_after_write(*args):
            original_writer(*args)
            raise RuntimeError('injected after unpack')

        naming._write_packed_image_as_png = fail_after_write
        try:
            try:
                naming.write_or_copy_image_file(image, image.name, directory / 'failures')
            except RuntimeError as error:
                assert 'injected' in str(error)
            else:
                raise AssertionError('write failure not raised')
        finally:
            naming._write_packed_image_as_png = original_writer
        assert payload(image) == original
        assert image.filepath == '' and image.file_format == 'JPEG'
        assert image.is_dirty == dirty
        assert_pixels(image, expected_pixels)
        assert naming.PACKED_RESTORE_BACKUP_PROP not in image

    # Failed Restore keeps the prepared source usable and retains its backup
    # for retry, including edits made after Prepare.
    image = fixture(directory, 'RestoreFailure')
    original = payload(image)
    naming.remember_image_path(image)
    target = naming.write_or_copy_image_file(image, image.name, directory / 'failures')
    image.pixels[:] = [0.0, 1.0, 1.0, 1.0] * 16
    image.update()
    expected_pixels = pixels(image)
    original_pack = naming._pack_image_bytes

    def fail_after_pack(*args):
        original_pack(*args)
        raise RuntimeError('injected after repack')

    naming._pack_image_bytes = fail_after_pack
    try:
        try:
            naming.restore_image_path(image)
        except RuntimeError as error:
            assert 'injected' in str(error)
        else:
            raise AssertionError('restore failure not raised')
    finally:
        naming._pack_image_bytes = original_pack
    assert image.packed_file is None and Path(image.filepath_raw) == target
    assert image.is_dirty
    assert_pixels(image, expected_pixels)
    assert payload(image[naming.PACKED_RESTORE_BACKUP_PROP]) == original
    assert naming.restore_image_path(image)
    assert_pixels(image, expected_pixels)
    assert payload(image[naming.PACKED_ORIGINAL_RECOVERY_PROP]) == original

    # New edits, whether left dirty or explicitly repacked, take precedence
    # over the immutable first-Prepare recovery snapshot on later exports.
    for repack in (False, True):
        name = 'EditedAgain' + str(repack)
        image = fixture(directory, name)
        original = payload(image)
        naming.remember_image_path(image)
        image.pixels[:] = [0.0, 1.0, 0.0, 1.0] * 16
        image.update()
        naming.write_or_copy_image_file(image, name, directory / 'edited')
        image.pixels[:] = [0.0, 0.0, 1.0, 1.0] * 16
        image.update()
        expected_pixels = pixels(image)
        if repack:
            image.pack()
        target = naming.write_or_copy_image_file(image, name, directory / 'edited')
        assert_pixels(image, expected_pixels)
        exported = bpy.data.images.load(str(target), check_existing=False)
        assert_pixels(exported, expected_pixels)
        bpy.data.images.remove(exported)
        save_reload(directory / (name + '_prepared.blend'))
        image = bpy.data.images[name]
        assert naming.restore_image_path(image)
        assert_pixels(image, expected_pixels)
        assert payload(image[naming.PACKED_ORIGINAL_RECOVERY_PROP]) == original
        save_reload(directory / (name + '_restored.blend'))
        assert_pixels(bpy.data.images[name], expected_pixels)

    # A failed repeated export rolls back to the NEW pre-call packed source,
    # independently of the first Prepare's Restore backup.
    image = fixture(directory, 'RepeatWriteFailure')
    naming.remember_image_path(image)
    naming.write_or_copy_image_file(image, image.name, directory / 'failures')
    image.pixels[:] = [0.0, 0.0, 1.0, 1.0] * 16
    image.update()
    image.pack()
    current_packed = payload(image)
    expected_pixels = pixels(image)
    original_writer = naming._write_packed_image_as_png
    naming._write_packed_image_as_png = fail_after_write
    try:
        try:
            naming.write_or_copy_image_file(image, image.name, directory / 'failures')
        except RuntimeError as error:
            assert 'injected' in str(error)
        else:
            raise AssertionError('repeat write failure not raised')
    finally:
        naming._write_packed_image_as_png = original_writer
    assert payload(image) == current_packed
    assert_pixels(image, expected_pixels)
    assert naming.PACKED_RESTORE_BACKUP_PROP in image

    # Restore rollback must also preserve a current packed source exactly.
    original_pack = naming._pack_image_bytes
    pack_calls = 0

    def fail_first_pack(*args):
        global pack_calls
        pack_calls += 1
        original_pack(*args)
        if pack_calls == 1:
            raise RuntimeError('injected first restore pack')

    naming._pack_image_bytes = fail_first_pack
    try:
        try:
            naming.restore_image_path(image)
        except RuntimeError as error:
            assert 'injected' in str(error)
        else:
            raise AssertionError('packed restore failure not raised')
    finally:
        naming._pack_image_bytes = original_pack
    assert payload(image) == current_packed
    assert_pixels(image, expected_pixels)
    assert naming.PACKED_RESTORE_BACKUP_PROP in image

    # Float RGBA snapshots preserve HDR/negative channels and fractional alpha
    # through EXR packing and prepared/restored .blend reloads.
    image = bpy.data.images.new('FloatPacked', width=2, height=2, float_buffer=True, alpha=True)
    image.use_fake_user = True
    image.pixels[:] = [1.0, 0.0, 0.0, 1.0] * 4
    image.pack()
    original = payload(image)
    original_alpha = image.alpha_mode
    naming.remember_image_path(image)
    image.pixels[:] = [0.12345678, -0.31234568, 2.9876542, 0.43219876] * 4
    image.update()
    expected_pixels = pixels(image)
    naming.write_or_copy_image_file(image, image.name, directory / 'float')
    save_reload(directory / 'FloatPacked_prepared.blend')
    image = bpy.data.images['FloatPacked']
    assert naming.restore_image_path(image)
    assert_pixels(image, expected_pixels)
    assert image.alpha_mode == 'CHANNEL_PACKED'
    recovery = image[naming.PACKED_ORIGINAL_RECOVERY_PROP]
    assert payload(recovery) == original and recovery.alpha_mode == original_alpha
    save_reload(directory / 'FloatPacked_restored.blend')
    assert_pixels(bpy.data.images['FloatPacked'], expected_pixels)

    # Ordinary unpacked textures still restore their original path with no
    # packed ownership state introduced.
    image = fixture(directory, 'Unpacked', packed=False)
    original_path = image.filepath_raw
    naming.remember_image_path(image)
    target = naming.write_or_copy_image_file(image, image.name, directory / 'unpacked')
    naming.write_or_copy_image_file(image, image.name, directory / 'unpacked')
    assert naming.PACKED_RESTORE_BACKUP_PROP not in image
    assert naming.restore_image_path(image)
    assert image.packed_file is None and image.filepath_raw == original_path
    assert Path(original_path).is_file() and target.is_file()
    addon_utils.disable('ue_unique_export_names_addon', default_set=False)

print('packed image restore smoke: OK')
