#!/usr/bin/env python3
"""Supplement circuit inspection using explicitly selected Qiskit versions.

Modes: parameters (current environment, in-scope QPY files), pickles (six known
Qiskit caches). All data/ directories except data/cplex/ are excluded.
Only the pickles mode unpickles real classes: approved packaged Qiskit classes,
builtins.getattr and functools.partial; source-project classes are never run.
"""
import argparse
from collections import Counter
import gzip
import importlib
import json
import os
from pathlib import Path
import pickle
import warnings

from qiskit import QuantumCircuit, qpy
from audit_source_data import EXCLUDED, circuit_record, scalar_summary


class CircuitUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module.startswith('qiskit.') or (module, name) in {('builtins', 'getattr'), ('functools', 'partial')}:
            return vars(importlib.import_module(module))[name]
        raise pickle.UnpicklingError(f'unsupported circuit class {module}.{name}')


def parameters(circuit):
    groups = {}
    for instruction in circuit.data:
        operation = instruction.operation
        if operation.params:
            values = json.dumps([scalar_summary(p) for p in operation.params], sort_keys=True)
            groups.setdefault(operation.name, Counter())[values] += 1
    return {name: [{'values': json.loads(values), 'instruction_count': count} for values, count in sorted(counter.items())]
            for name,counter in sorted(groups.items())}


def locate_circuits(value, location='$'):
    if isinstance(value, QuantumCircuit):
        return [(location, value)]
    if isinstance(value, (list, tuple)):
        return [found for i,child in enumerate(value) for found in locate_circuits(child, f'{location}[{i}]')]
    if isinstance(value, dict):
        return [found for key,child in value.items() for found in locate_circuits(child, f'{location}[{key!r}]')]
    return []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['parameters','pickles'])
    parser.add_argument('root', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    records = []
    if args.mode == 'parameters':
        paths = []
        for base, dirs, names in os.walk(args.root):
            dirs[:] = sorted(d for d in dirs if d not in EXCLUDED)
            if Path(base) == args.root / 'data':
                dirs[:] = [d for d in dirs if d == 'cplex']
                names = []
            paths.extend(Path(base) / n for n in sorted(names) if n.endswith('.qpy'))
    else:
        paths = [args.root / 'store' / (name + '.pkl') for name in [
            '03bea3382f8006ad','4555b61896d340f5','54cdc3c9e8c1cacd',
            '58fc289149aa8dc2','5daaf215c5f8ef18','5ea09418178c344d']]
    for path in paths:
        record = {'path': str(path.relative_to(args.root))}
        with warnings.catch_warnings(record=True), path.open('rb') as stream:
            if args.mode == 'pickles':
                value = CircuitUnpickler(stream).load()
                found = locate_circuits(value)
                record['container'] = type(value).__name__
            else:
                try:
                    circuits = qpy.load(stream)
                except Exception as error:
                    if args.mode != 'parameters':
                        raise
                    record['decoder_error'] = f'{type(error).__name__}: {error}'
                    records.append(record)
                    continue
                found = [(f'$[{i}]', c) for i,c in enumerate(circuits)]
            record['circuits'] = []
            for index,(location,circuit) in enumerate(found):
                if args.mode == 'parameters':
                    summary = {'index': index, 'name': circuit.name}
                else:
                    summary = circuit_record(circuit, index)
                record['circuits'].append({'location': location, **summary, 'instruction_parameter_values': parameters(circuit)})
        records.append(record)
        if len(records) % 100 == 0:
            print(f'Inspected {len(records)} circuit files', flush=True)
    payload = json.dumps(records, default=scalar_summary, sort_keys=True, separators=(',', ':')).encode()
    if args.output.suffix == '.gz':
        with gzip.GzipFile(str(args.output), 'wb', mtime=0) as stream:
            stream.write(payload)
    else:
        args.output.write_text(json.dumps(records, default=scalar_summary, indent=2) + '\n')
    print(json.dumps({'files': len(records), 'circuits': sum(len(r.get('circuits', [])) for r in records)}))


if __name__ == '__main__':
    main()
