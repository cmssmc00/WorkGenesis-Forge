"""Recompute demo facts independently from the unchanged USGS CSV, not its oracle."""
import csv,json,hashlib,sys
from collections import defaultdict
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
from openpyxl import load_workbook
if len(sys.argv)!=2:raise SystemExit('Usage: python qa/recompute_source_control.py PATH_TO_DEMO_OUTPUT')
root=Path(sys.argv[1])
anchors=list((root/'scenario/public/anchors').glob('*.csv'))
assert len(anchors)==1
with anchors[0].open(newline='') as f:
 reader=csv.DictReader(f);headers=reader.fieldnames;rows=list(reader)
valid=[(row,Decimal(row['mag'])) for row in rows if row['mag'].strip()]
assert all(v.is_finite() for _,v in valid)
summary=json.loads((root/'control/workspace/output/summary.json').read_text())
expected={'event_count':len(rows),'valid_magnitude_count':len(valid),'mean_magnitude':str((sum(v for _,v in valid)/len(valid)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)),'threshold_event_count':sum(v>=Decimal('4.5') for _,v in valid),'largest_magnitude':str(max(v for _,v in valid)),'largest_event_id':sorted((v,row['id']) for row,v in valid)[-1][1],'observed_start':min(r['time'] for r in rows),'observed_end':max(r['time'] for r in rows)}
assert all(summary[k]==v for k,v in expected.items()),(summary,expected)
book=load_workbook(root/'control/workspace/output/catalog_analysis.xlsx',data_only=False)
workbook_summary=dict(list(book['Summary'].values)[1:]);assert all(workbook_summary[k]==v for k,v in expected.items())
raw=list(book['Source Events'].values);assert list(raw[0])==headers;assert [list(r) for r in raw[1:]]==[[None if v=='' else v for v in row.values()] for row in rows]
days=defaultdict(list)
for row,v in valid:days[row['time'][:10]].append(v)
expected_daily=[[date,len(values),len(values),sum(v>=Decimal('4.5') for v in values),str(max(values))] for date,values in sorted(days.items())]
assert [list(r) for r in list(book['Daily'].values)[1:]]==expected_daily
print(json.dumps({'verification':'independent_recomputation_passed','rows':len(rows),'source_sha256':hashlib.sha256(anchors[0].read_bytes()).hexdigest(),'expected':expected,'daily_rows':expected_daily,'checks':['source byte hash','all eight scalar facts','all source rows and headers','every daily aggregate']},indent=2))
