"""Present so that pytest puts the repository root on `sys.path`.

`data/` and `evals/` are deliberately not inside `src/`: they are not part of the installed
package, because a synthetic generator and a set of labelled scenarios have no business
shipping in a wheel. They still have to be importable by the suite, and pytest's `prepend`
import mode inserts the rootdir when it finds a conftest here.

An empty file with a reason beats a `sys.path` manipulation inside three test modules.
"""
