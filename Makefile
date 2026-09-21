# Makefile cho hệ thống RAG sinh báo giá Kaiyo Việt Nam
# Dùng:  make <target>

PY      := .venv/bin/python
MAIN    := main.py

.PHONY: help setup build-index run run-file watch force clean

help: ## Hiển thị danh sách lệnh
	@echo "Các lệnh khả dụng:"
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk -F '[:,]' '{printf "  make %-12s %s\n", $$1, $$3}'

setup: ## Tạo virtualenv và cài dependencies
	python3 -m venv .venv
	$(PY) -m pip install -r requirements.txt

build-index: ## (Re)build RAG index từ golden-data
	$(PY) $(MAIN) build-index

run: ## Xử lý toàn bộ file trong data/ (bỏ qua file đã có report)
	$(PY) $(MAIN) run

force: ## Xử lý toàn bộ data/ và ghi đè report đã có
	$(PY) $(MAIN) run --force

run-file: ## Xử lý 1 file, VD: make run-file FILE=data/BTN1205926.xlsx
	$(PY) $(MAIN) run --file $(FILE)

watch: ## Chạy watcher nền, tự sinh report khi có file mới trong data/
	$(PY) $(MAIN) watch

clean: ## Xóa report và cache index đã sinh
	rm -f reports/*.xlsx
	rm -rf rag_index/
