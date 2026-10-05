#!/usr/bin/env python3
"""Inspect auxiliary source artifacts and refresh targeted census records.

Run after audit_source_data.py and audit_source_payloads.py, with the same root
and output directory. Does not modify the source project or execute its classes.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import lzma
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

from audit_source_data import in_scope, inspect_file, inspect_pickle, scalar_summary

TERMS = re.compile(r'\b(?:shots?|samples?|counts?|circuit|layout|calibration|backend|QAOA|Runtime|job|qubit|gamma|beta|ibm_boston)\b', re.I)


def write_json(path, value):
    payload = json.dumps(value, default=scalar_summary, sort_keys=True, separators=(',', ':')).encode()
    if path.suffix == '.gz':
        with gzip.GzipFile(str(path), 'wb', mtime=0) as stream:
            stream.write(payload)
    else:
        path.write_text(json.dumps(value, default=scalar_summary, indent=2) + '\n')


def notebook_records(root, files):
    records = []
    for f in files:
        if f['extension'] != '.ipynb':
            continue
        nb = json.loads((root / f['path']).read_text())
        cells = []
        for i, cell in enumerate(nb['cells']):
            source = ''.join(cell.get('source', []))
            output = ''.join(''.join(o.get('text', [])) + ''.join(o.get('data', {}).get('text/plain', [])) for o in cell.get('outputs', []))
            cells.append({'index': i, 'type': cell['cell_type'], 'source_characters': len(source),
                          'text_output_characters': len(output), 'data_terms': sorted({s.lower() for s in TERMS.findall(source + output)})})
        records.append({'path': f['path'], 'cells': cells})
    return records


def document_records(root, files):
    records = []
    for f in files:
        path = root / f['path']
        if f['extension'] == '.pptx':
            with zipfile.ZipFile(path) as archive:
                slides = []
                for name in sorted(archive.namelist()):
                    if re.fullmatch(r'ppt/slides/slide\d+\.xml', name):
                        text = ' '.join(n.text or '' for n in ET.fromstring(archive.read(name)).iter() if n.tag.endswith('}t'))
                        slides.append({'slide': name, 'text_characters': len(text), 'data_terms': sorted({s.lower() for s in TERMS.findall(text)})})
                records.append({'path': f['path'], 'slides': slides,
                                'embedded_files': [n for n in archive.namelist() if '/embeddings/' in n],
                                'media_count': sum(n.startswith('ppt/media/') for n in archive.namelist())})
        elif f['extension'] == '.pdf':
            result = subprocess.run(['pdftotext', str(path), '-'], capture_output=True, text=True)
            records.append({'path': f['path'], 'text_extraction_exit_code': result.returncode,
                            'text_characters': len(result.stdout),
                            'data_terms': sorted({s.lower() for s in TERMS.findall(result.stdout)})})
    return records


def embedded_workbooks(root, documents):
    records = []
    for doc in documents:
        if not doc.get('embedded_files'):
            continue
        with zipfile.ZipFile(root / doc['path']) as outer:
            for name in doc['embedded_files']:
                if not name.endswith('.xlsx'):
                    continue
                data = outer.read(name)
                with zipfile.ZipFile(io.BytesIO(data)) as book:
                    shared = []
                    if 'xl/sharedStrings.xml' in book.namelist():
                        shared = [''.join(n.itertext()) for n in ET.fromstring(book.read('xl/sharedStrings.xml'))]
                    workbook = ET.fromstring(book.read('xl/workbook.xml'))
                    names = [s.attrib['name'] for s in workbook.findall('.//{*}sheet')]
                    sheets = []
                    for i, sheet_path in enumerate(sorted(n for n in book.namelist() if n.startswith('xl/worksheets/sheet') and n.endswith('.xml'))):
                        cells = []
                        for cell in ET.fromstring(book.read(sheet_path)).findall('.//{*}c'):
                            value, formula, inline = cell.find('{*}v'), cell.find('{*}f'), cell.find('{*}is')
                            v = value.text if value is not None else None
                            if cell.attrib.get('t') == 's' and v is not None:
                                v = shared[int(v)]
                            if inline is not None:
                                v = ''.join(inline.itertext())
                            if v is not None or formula is not None:
                                cells.append({'cell': cell.attrib['r'], 'type': cell.attrib.get('t', 'n'),
                                              'value': v, 'formula': formula.text if formula is not None else None})
                        sheets.append({'xml_path': sheet_path, 'name': names[i], 'cells': cells})
                    records.append({'presentation': doc['path'], 'embedded_path': name,
                                    'sha256': hashlib.sha256(data).hexdigest(), 'sheets': sheets})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('output_dir', type=Path)
    args = parser.parse_args()
    root, out = args.root.resolve(), args.output_dir
    inventory_path = out / 'source-data-inventory.json.gz'
    inventory = json.loads(gzip.decompress(inventory_path.read_bytes()))
    if any(not in_scope(f['path']) for f in inventory['files']):
        raise ValueError('Census contains content outside the authorized source scope')
    auxiliary = []
    for f in inventory['files']:
        path, relative = root / f['path'], Path(f['path'])
        if 'networkx.classes.' in f.get('inspection', {}).get('metadata_decode_limit', ''):
            f['inspection'] = inspect_pickle(path)
        if f['path'] in {'store/index.csv', 'store/time.csv', 'store/clustering_patches.csv'}:
            f['inspection'] = inspect_file(path, relative)
        if f['path'].startswith('data/cplex/') and f['extension'] in {'.dat', '.mod', '.txt'}:
            text = path.read_text()
            auxiliary.append({'path': f['path'], 'kind': 'solver_artifact',
                              'assignment_fields': sorted(set(re.findall(r'(?m)^\s*([A-Za-z_]\w*)\s*=', text))),
                              'characters': len(text),
                              'data_terms': sorted({s.lower() for s in TERMS.findall(text)})})
        if f['extension'] == '.stale':
            original = relative.with_suffix('')
            if original.suffix == '.json':
                # inspect_file selects a parser by the staged suffix.
                data = json.loads(path.read_text())
                auxiliary.append({'path': f['path'], 'kind': 'historical_graph_json',
                                  'keys': sorted(data), 'n': data.get('n'),
                                  'edge_count_upper_triangle': sum(w != 0 for i,row in enumerate(data.get('weights', [])) for w in row[i+1:])})
            else:
                auxiliary.append({'path': f['path'], 'kind': 'historical_solver_input', 'leading_text': path.read_text()[:256]})
        if f['extension'] in {'.gz', '.xz'} and f['path'].startswith('data/'):
            opener = gzip.open if f['extension'] == '.gz' else lzma.open
            with opener(path, 'rb') as stream:
                leading = stream.read(512)
            auxiliary.append({'path': f['path'], 'kind': 'compressed_benchmark_input',
                              'leading_bytes_hex': leading[:16].hex(),
                              'leading_text': leading.decode('utf-8', errors='replace')[:256]})
    write_json(inventory_path, inventory)
    payload_path = out / 'source-data-payloads.json.gz'
    payloads = json.loads(gzip.decompress(payload_path.read_bytes()))
    write_json(payload_path, payloads)
    write_json(out / 'source-data-notebooks.json', notebook_records(root, inventory['files']))
    documents = document_records(root, inventory['files'])
    write_json(out / 'source-data-documents.json', documents)
    write_json(out / 'source-data-embedded-workbooks.json', embedded_workbooks(root, documents))
    write_json(out / 'source-data-auxiliary.json.gz', auxiliary)
    legacy = []
    execution_ids = {'36c31f817ef1a4e9', '45002481120aeab0', '48837100bddef0b7', '6724b1c7092465c0', '6af659c4996faf4f'}
    for f in inventory['files']:
        if f['path'].startswith('store/') and Path(f['path']).stem in execution_ids:
            for index, item in enumerate(f['inspection']['legacy_summary']['items']):
                factor, stats = item['items']
                legacy.append({'path': f['path'], 'key': None, 'index': index, 'kind': 'legacy_execution_counts',
                               'noise_factor': factor, 'bit_width': 52 if Path(f['path']).stem == '48837100bddef0b7' else 4,
                               'shots': stats['value_sum'], 'unique_samples': stats['entries'], 'max_occurrences': stats['value_max']})
    write_json(out / 'source-data-legacy-counts.json', legacy)
    fields = ['path', 'key', 'index', 'kind', 'bit_width', 'noise_factor', 'shots', 'unique_samples', 'max_occurrences', 'entries', 'sum', 'min', 'max', 'negative_entries']
    with (out / 'source-shot-catalog.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        for record in payloads['samples']:
            writer.writerow({**record, 'kind': record.get('kind', 'observed_samples')})
        writer.writerows(legacy)
    print(json.dumps({'auxiliary_artifacts': len(auxiliary), 'catalog_collections': len(payloads['samples']),
                      'remaining_opaque_decode_limits': sum('metadata_decode_limit' in f.get('inspection', {}) for f in inventory['files'])}))


if __name__ == '__main__':
    main()
