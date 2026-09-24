"""High parent/GPro dependency must never be auto-linked as low export data."""
import bpy,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ue_unique_export_names_addon import painter_sync as sync
from ue_unique_export_names_addon.constants import AUTO_PAINTER_EXPORT_LINK_PROP
bpy.ops.wm.read_factory_settings(use_empty=True)
scene=bpy.context.scene
def coll(name,parent):
    c=bpy.data.collections.new(name);parent.children.link(c);return c
def obj(name,c,mesh=False):
    o=bpy.data.objects.new(name,bpy.data.meshes.new(name) if mesh else None);c.objects.link(o);return o
root=coll('Baking',scene.collection);low=coll('low',root);high=coll('high',root);exp=coll('Export',scene.collection)
hi=obj('Jacket_high_01',high,True);lo=obj('Jacket_low',low,True);lo.parent=hi
manual=obj('ManualExport',exp,True)
hi[AUTO_PAINTER_EXPORT_LINK_PROP]=True;exp.objects.link(hi)
sync.sync_painter_export(scene)
assert lo in set(exp.objects) and hi not in set(exp.objects) and manual in set(exp.objects)
assert lo.parent==hi,'The synchronization must never reparent the user hierarchy'
source=coll('Unlinked_GPro_Source',scene.collection);scene.collection.children.unlink(source)
inner=obj('ArbitraryInternalName',source,True)
instance=obj('HighCollectionInstance',high)
instance.instance_type='COLLECTION';instance.instance_collection=source
assert inner in sync._non_export_bake_objects(low)
# GroupPro mesh containers expose their source as a Geometry Nodes input.
gn=bpy.data.node_groups.new('GPro_Instance','GeometryNodeTree')
sock=gn.interface.new_socket(name='Instanced Collection',in_out='INPUT',socket_type='NodeSocketCollection')
mod=hi.modifiers.new('GPro_Instance','NODES');mod.node_group=gn
mod.properties.inputs[sock.identifier]['value']=source
high.objects.unlink(instance)
assert inner in sync._non_export_bake_objects(low)
lo.parent=inner;sync.sync_painter_export(scene)
assert inner not in set(exp.objects)
# A later role-only move must invalidate a previously cached hierarchy.
ancestor=obj('FutureHigh',scene.collection,True);lo.parent=ancestor
sync.sync_painter_export(scene);assert ancestor in set(exp.objects)
high.objects.link(ancestor)
assert sync._current_collection_membership_signature(scene)!=sync._painter_export_collection_signature
sync.sync_painter_export(scene);assert ancestor not in set(exp.objects)
assert manual in set(exp.objects)
print('HIGH_EXPORT_GUARD_PASS')
