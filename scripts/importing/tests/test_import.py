import numpy as np
import pytest
from openshots.binary import decode_block

from import_source_data import Importer, cycle_solutions, np_blocks


@pytest.mark.parametrize("width", [1, 7, 8, 9, 49, 100, 255, 256])
def test_numpy_import_preserves_numeric_states_and_counts(width):
    rng = np.random.default_rng(4)
    values = rng.integers(0, 2, (500, width), dtype=np.uint8)
    values = np.concatenate((values, values[:100]))
    packed = np.packbits(values, axis=1, bitorder="big")
    expected = {}
    for row in values:
        state = int("".join(map(str, row)), 2)
        expected[state] = expected.get(state, 0) + 1
    actual = [
        item
        for block in np_blocks(packed, width)
        for item in decode_block(block, width=width)
    ]
    assert actual == sorted(expected.items())


def test_signed_cycle_solution_is_exact():
    edges = [[i, (i + 1) % 6, 3 if i < 2 else -1] for i in range(6)]
    best, states = cycle_solutions(6, edges)

    def objective(s):
        return sum(w for a, b, w in edges if (s >> a & 1) != (s >> b & 1))

    assert best == max(map(objective, range(64)))
    assert all(objective(s) == best for s in states)


def test_scope_guard_never_opens_excluded_path(tmp_path):
    importer = object.__new__(Importer)
    importer.root = tmp_path
    importer.manifest = {}
    for path in [
        "figures/calibration.json",
        "backups/a",
        "data/qopt-best-practices/a",
        "store/hex_swaps_mis_shots_bad/a",
        "../../secret",
    ]:
        with pytest.raises(ValueError, match="scope"):
            importer.read(path)
