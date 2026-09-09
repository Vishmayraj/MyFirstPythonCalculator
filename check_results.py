"""Check which plates are still failing after fixes"""
import json

data = json.load(open('anpr_indian_test_results/results.json', encoding='utf-8'))

print("Plates that don't match GT (failures):")
print("=" * 80)
for img in data['images']:
    for p in img['plates']:
        gt = p.get('gt')
        if gt and not p.get('exact'):
            print(f"  {img['image'][:40]:40s} OCR={p['text']:15s} GT={gt:15s} IoU={p.get('gt_iou','')}")

print()
print("Summary:")
total_gt = sum(len([p for p in img['plates'] if p.get('gt')]) for img in data['images'])
total_exact = sum(len([p for p in img['plates'] if p.get('exact')]) for img in data['images'])
print(f"  GT plates: {total_gt}, Exact matches: {total_exact}, Rate: {100*total_exact/total_gt:.1f}%")

# Also check the KA09 image specifically
print()
print("Checking KA09 image specifically:")
for img in data['images']:
    if '000490' in img['image']:
        for p in img['plates']:
            print(f"  Plate: text={p['text']}, gt={p.get('gt')}, exact={p.get('exact')}")
