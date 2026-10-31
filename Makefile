include .env

# Otherwise `make scp` doesn't work, idk.
ifeq ($(OS),Windows_NT)
    SHELL := pwsh.exe
else
    SHELL := pwsh
endif
.SHELLFLAGS := -NoProfile -Command 

.PHONY: run
.SILENT: run
run:
	uv run src/main.py


.PHONY: ruff
.SILENT: ruff
ruff:  # Run the Ruff's linter 
	uv run ruff check $(sources)
	uv run ruff format --check $(sources)


.PHONY: ty
.SILENT: ty
ty:  # Run typechecker (ty)
	uv run ty check .


.PHONY: check
.SILENT: check
check:  # Run both typechecker and linter (simply calls "make ruff" and "make ty")
	Write-Host 'Running "make ruff"' -ForegroundColor Magenta
	make ruff
	Write-Host 'Running "make ty"' -ForegroundColor Magenta
	make ty


.PHONY: update
.SILENT: update
update:  # Update dependencies
	uv lock --upgrade
	uv sync