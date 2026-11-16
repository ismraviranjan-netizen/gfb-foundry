.PHONY: bundle test evals evals-live generate monitor sweep plan layer ledger clean radar story console demo
test:            ; python3 -m pytest -q
evals:           ; python3 evals/run_evals.py && python3 evals/run_rebalancing_evals.py && python3 evals/run_sensing_evals.py && python3 evals/run_queue_evals.py && python3 evals/run_tower_evals.py && python3 evals/run_control_room_evals.py && python3 evals/run_carbon_evals.py
evals-live:      ; python3 evals/run_evals.py --live --only C01,C04,C07,C10,C13,T26 && python3 evals/run_rebalancing_evals.py --live --only C01,N12,T17 && python3 evals/run_tower_evals.py --live --only E01,E02
generate:        ; python3 -m foundry.cli generate data/out && python3 -m foundry.cli generate data/sensing --sensing
monitor:         ; python3 -m foundry.cli monitor --week 12
sweep:           ; python3 -m foundry.cli sweep --week 12
plan:            ; python3 -m foundry.cli plan --dc CHN --sku MN-1L --week 12
layer:           ; mkdir -p deploy/layer/python deploy/build && (uv pip install --quiet --target deploy/layer/python --python-platform x86_64-manylinux_2_28 --python-version 3.13 pyyaml tzdata || python3 -m pip install --quiet --target deploy/layer/python --platform manylinux2014_x86_64 --only-binary=:all: --python-version 3.13 --implementation cp pyyaml tzdata) && cd deploy/layer && zip -qr ../build/layer.zip python && echo layer built
bundle:          ; python3 deploy/bundle.py
ledger:          ; python3 ops/ledger.py
radar:           ; python3 -m foundry.cli radar --week 12 && python3 -m foundry.cli radar --position
story:           ; python3 -m foundry.cli story week12 --messages
console:         ; python3 -m foundry.console.server --storyline blank --port 8080
demo:            ; python3 -m foundry.console.snapshot --html out/console_demo.html
clean:           ; rm -rf runs data/out data/sensing deploy/build deploy/layer evals/*results.json
