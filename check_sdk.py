"""Optional read-only API symbol check. Does not load Fusion or prove runtime behavior."""
import argparse
import ast
from pathlib import Path

CONTRACTS = {
    'core': {
        'Application': 'data documents activeDocument registerCustomEvent fireCustomEvent unregisterCustomEvent',
        'Data': 'dataProjects activeProject',
        'DataFile': 'id versionId versionNumber name parentProject parentFolder fileExtension dateModified hasChildReferences childReferences isComplete download isConfiguration isConfiguredDesign',
        'Document': 'dataFile isModified close activate',
        'Documents': 'open count item',
        'ValidateInputsEventArgs': 'inputs areInputsValid',
        'CommandEventArgs': 'command executeFailed executeFailedMessage',
        'InputChangedEventArgs': 'input inputs',
        'Command': 'okButtonText destroy execute inputChanged validateInputs',
        'ProgressDialog': 'maximumValue message wasCancelled cancelButtonText isCancelButtonShown',
        'DropDownStyles': 'CheckBoxDropDownStyle TextListDropDownStyle',
        'DialogResults': 'DialogOK DialogYes',
    },
    'fusion': {'FusionDocument': 'cast design', 'ExportManager': 'createFusionArchiveExportOptions createSTEPExportOptions execute'},
}


def check(sdk):
    count = 0
    for module, classes in CONTRACTS.items():
        tree = ast.parse((Path(sdk) / (module + '.py')).read_text(encoding='utf-8-sig'))
        definitions = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        assignments = {(target.value.id, target.attr) for node in tree.body if isinstance(node, ast.Assign)
                       for target in node.targets if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)}
        for name, members in classes.items():
            body = definitions[name].body
            found = {node.name for node in body if isinstance(node, ast.FunctionDef)}
            found |= {target.id for node in body if isinstance(node, ast.Assign)
                      for target in node.targets if isinstance(target, ast.Name)}
            for member in members.split():
                if member not in found and '_get_' + member not in found and (name, member) not in assignments:
                    raise ValueError(f'Missing API: {module}.{name}.{member}')
                count += 1
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('adsk_directory', help='Installed Fusion Api/Python/packages/adsk directory')
    args = parser.parse_args()
    print('Installed Autodesk SDK class/member checks passed:', check(args.adsk_directory))
