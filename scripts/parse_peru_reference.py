import re,csv,glob,os
from collections import defaultdict
TRDIR="/sessions/epic-exciting-gates/mnt/.claude/projects/C--Users-faq-l-AppData-Roaming-Claude-local-agent-mode-sessions-07a6deee-c726-443d-8161-352cb843cf2d-e0bb40d6-dd3f-427f-abca-ddae601a6a98-local-d93ed3e0-66fc-4a08-8ec2-71ae10daee66-outputs/b6e812a8-f518-46c9-9bc1-32b0112d7d9a/tool-results"
def find(ts):
    g=glob.glob(os.path.join(TRDIR,f"*web_fetch-{ts}.txt"));return g[0] if g else None
# manifest from fetch order: timestamp -> (anio, mes)
manifest=[("1781119595277",2025,1),("1781119697735",2025,2),("1781119698764",2025,3),
("1781119699891",2025,4),("1781119702769",2025,5),("1781119596522",2025,6),
("1781119721489",2025,7),("1781119724439",2025,8),("1781119725769",2025,9),
("1781119705835",2025,10),("1781119708803",2025,11),("1781119597979",2025,12),
("1781119729414",2026,1),("1781119730919",2026,2),("1781119732062",2026,3)]
SEG=['Automóviles, SW','Pick up, furgonetas','Camionetas','SUV, todoterreno']
SEGN=['Automoviles/SW','Pickup/furgonetas','Camionetas','SUV/todoterreno']
hdr_re=re.compile(r'^Rank\. Marca (\d{4}) (\d{4}) Var\.% Part\.% \d{4}$')
row_re=re.compile(r'^(\d+)\s+(.+?)\s+([\d,]+)\s+([\d,]+)\s+(-?[\d.]+)%\s+([\d.]+)%$')
def parse(fp):
    L=open(fp,encoding='utf-8').read().splitlines()
    gi=None
    for i in range(len(L)-3):
        if all(L[i+k].strip()==SEG[k] for k in range(4)):gi=i;break
    if gi is None:return None,None
    yr=None;out=[];si=0;k=gi+4
    while si<4 and k<len(L):
        hm=hdr_re.match(L[k].strip())
        if hm:
            yr=int(hm.group(2));k+=1;rows=[]
            while k<len(L):
                m=row_re.match(L[k].strip())
                if m:
                    g=m.groups();rows.append((SEGN[si],int(g[0]),g[1].strip(),int(g[2].replace(',','')),int(g[3].replace(',','')),float(g[4]),float(g[5])));k+=1
                elif L[k].strip().startswith('Total'):k+=1;break
                elif L[k].strip().startswith('Otros'):k+=1
                else:
                    if rows:break
                    k+=1
            out+=rows;si+=1
        else:k+=1
    return out,yr
seg_rows=[];fails=[]
for ts,anio,mes in manifest:
    fp=find(ts)
    if not fp:fails.append((anio,mes,'no file'));continue
    res,yr=parse(fp)
    if not res:fails.append((anio,mes,'parse fail'));continue
    if yr!=anio:fails.append((anio,mes,f'YEAR MISMATCH got {yr}'));continue
    for seg,rk,mk,up,uc,var,sh in res:
        seg_rows.append([anio,mes,seg,mk,uc])  # uc = acumulado a ese mes
print("meses parseados:",len({(r[0],r[1]) for r in seg_rows}),"| fallos:",fails)
# national accumulated per (anio,mes,marca)
acc=defaultdict(int)
for anio,mes,seg,mk,uc in seg_rows: acc[(anio,mes,mk)]+=uc
# monthly delta within year
months=sorted({(a,m) for a,m,_ in acc})
marcas=sorted({k[2] for k in acc})
out=[]
for mk in marcas:
    for (a,m) in months:
        cur=acc.get((a,m,mk))
        if cur is None: continue
        prev=acc.get((a,m-1,mk)) if m>1 else 0
        mensual = cur-prev if prev is not None else None
        out.append(['Peru',a,m,mk,cur,mensual if mensual is not None and mensual>=0 else ''])
with open('history/peru_nacional_mensual.csv','w',newline='',encoding='utf-8') as f:
    w=csv.writer(f);w.writerow(['pais','anio','mes','marca','unid_acum','unid_mes']);w.writerows(out)
# sample: Toyota & Changan monthly
for brand in ['Toyota','Changan','Byd']:
    s=[r for r in out if r[3]==brand]
    print(brand,'->',[(f"{r[1]}-{r[2]:02d}",r[5]) for r in s])
print("filas serie:",len(out),"| marcas:",len(marcas))
