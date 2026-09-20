.DEFAULT_GOAL := run

PYTHON ?= .venv/bin/python
PIP    ?= .venv/bin/pip
PORT   ?= 8501

.PHONY: help venv install run dev test check-config compile clean

help:
	@echo "Muc tieu khả dung:"
	@echo "  make run          - Chay app Streamlit (mac dinh, port $(PORT))"
	@echo "  make dev          - Chay app o che do offline (USE_SUPABASE_DB=0)"
	@echo "  make install      - Tao venv va cai thu vien"
	@echo "  make test         - Chay pytest offline"
	@echo "  make check-config - Kiem tra cau hinh Supabase"
	@echo "  make compile      - Kiem tra cu phap cac file chinh"
	@echo "  make clean        - Xoa cache"

venv:
	@if [ ! -d .venv ]; then python3 -m venv .venv; fi

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

run: venv
	$(PYTHON) -m streamlit run app.py --server.port $(PORT)

dev: venv
	USE_SUPABASE_DB=0 $(PYTHON) -m streamlit run app.py --server.port $(PORT)

test: venv
	USE_SUPABASE_DB=0 $(PYTHON) -m pytest -q

check-config: venv
	$(PYTHON) supabase/check_config.py

compile: venv
	PYTHONIOENCODING=utf-8 $(PYTHON) -m py_compile app.py backend/*.py

clean:
	rm -rf .pytest_cache .tmp_pytest* backend/__pycache__ __pycache__
