# уменьшить окно и батч
sed -i 's/input_size=256/input_size=128/' scripts/train_nhits_dualmove.py
sed -i 's/batch_size=64/batch_size=16/' scripts/train_nhits_dualmove.py

# включить смешанную точность (меньше VRAM)
sed -i "/NHITS(h=H/a \    precision='16-mixed'," scripts/train_nhits_dualmove.py
