"""Regression of archived GLM K4 CRLF bytes and scoped git whitespace policy.

No source data rewriting, no general git diff --check disabling.
"""
import hashlib
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
REL="research/edge/m17_glm_p4/aud_k4/k4_resultados_original.csv"
ORIGINAL_SHA256="c00fce39abef9600efeb4a38742e2a9fd5998a559a6203f45c2d173fdf5687a2"
ATTR=f"{REL} -text -diff"


def _git(path,*args):
    return subprocess.run(["git",*args],cwd=path,check=False,
                          capture_output=True,text=True)


def test_original_csv_bytes_and_exact_git_attribute():
    raw=(ROOT/REL).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==ORIGINAL_SHA256, "ORIGINAL_BYTES_MUTATED"
    assert b"\r\n" in raw and raw.count(b"\r\n")>=30
    attributes=(ROOT/".gitattributes").read_text(encoding="utf8").splitlines()
    assert ATTR in attributes
    assert sum(1 for x in attributes if x and not x.startswith("#"))==1


def test_git_diff_check_still_detects_other_trailing_whitespace(tmp_path):
    project=tmp_path/"archive-vs-code"
    project.mkdir()
    assert _git(project,"init","-q").returncode==0
    assert _git(project,"config","user.email","local.invalid@example.test").returncode==0
    assert _git(project,"config","user.name","Offline Regression").returncode==0
    (project/"README.md").write_text("initial\n")
    assert _git(project,"add",".").returncode==0
    assert _git(project,"commit","-qm","baseline").returncode==0
    base=_git(project,"rev-parse","HEAD").stdout.strip()
    original=project/REL
    original.parent.mkdir(parents=True)
    original.write_bytes((ROOT/REL).read_bytes())
    assert _git(project,"add",".").returncode==0
    assert _git(project,"commit","-qm","add unmodified original csv").returncode==0
    red=_git(project,"diff","--check",base,"HEAD")
    assert red.returncode!=0
    assert REL in red.stdout and "trailing whitespace" in red.stdout

    (project/".gitattributes").write_text(ATTR+"\n")
    assert _git(project,"add",".").returncode==0
    assert _git(project,"commit","-qm","scoped binary archive classification").returncode==0
    green=_git(project,"diff","--check",base,"HEAD")
    assert green.returncode==0, green.stdout
    assert hashlib.sha256(original.read_bytes()).hexdigest()==ORIGINAL_SHA256

    (project/"unrelated.py").write_text("x = 1  \n")
    assert _git(project,"add",".").returncode==0
    assert _git(project,"commit","-qm","introduce unrelated code whitespace defect").returncode==0
    code_red=_git(project,"diff","--check",base,"HEAD")
    assert code_red.returncode!=0
    assert "unrelated.py" in code_red.stdout
    assert REL not in code_red.stdout
