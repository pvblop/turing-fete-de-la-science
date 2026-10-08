"""Validate and execute all notebook cells; generated outputs stay in memory."""
from pathlib import Path
import nbformat
from nbclient import NotebookClient

root = Path(__file__).resolve().parents[1]
notebook = nbformat.read(root/'turing_photo_demo.ipynb', as_version=4)
nbformat.validate(notebook)
client = NotebookClient(notebook, timeout=180, kernel_name='python3', resources={'metadata': {'path': str(root)}})
client.execute()
errors = [output for cell in notebook.cells for output in cell.get('outputs', []) if output.output_type == 'error']
assert not errors
print(f'Notebook valid; executed all {len(notebook.cells)} cells without private files')
