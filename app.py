import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import re

# Sayfa Yapılandırması
st.set_page_config(page_title="Gözetmen Planlama Sistemi", layout="wide")
st.title("🏛️ Gözetmen Optimizasyon ve Görev Planlama Sistemi")

# --- YARDIMCI FONKSİYONLAR ---
def flexible_time_parse(time_str):
    if not time_str or pd.isna(time_str): return None, None
    s = str(time_str).replace('.', ':').strip()
    clean = re.sub(r'[^0-9:]', '', s)
    parts = clean.split(':')
    if len(parts) >= 4:
        bas = int(parts[0]) * 60 + int(parts[1])
        bit = int(parts[2]) * 60 + int(parts[3])
        return bas, bit
    elif '-' in str(time_str):
        p = str(time_str).split('-')
        return to_min(p[0]), to_min(p[1])
    return None, None

def to_min(time_str):
    if not time_str: return None
    try:
        clean = re.sub(r'[^0-9:]', ':', str(time_str).replace('.', ':')).strip()
        parts = clean.split(':')
        h, m = int(parts[0]), int(parts[1])
        return h * 60 + m
    except: return None

def normalize_day(text):
    if pd.isna(text): return None, -1
    t = str(text).upper()
    mapping = {"İ": "I", "I": "I", "Ş": "S", "Ğ": "G", "Ü": "U", "Ö": "O", "Ç": "C"}
    for k, v in mapping.items(): t = t.replace(k, v)
    t = re.sub(r'[^A-Z]', '', t)
    cmap = {"PAZARTESI": 0, "SALI": 1, "CARSAMBA": 2, "PERSEMBE": 3, "CUMA": 4, "CUMARTESI": 5, "PAZAR": 6}
    for k, v in cmap.items():
        if k in t: return k, v
    return None, -1

def parse_excel(file):
    df = pd.read_excel(file)
    df.columns = [c.strip().upper() for c in df.columns]
    raw_rows, current_week, prev_idx = [], 1, -1
    
    for _, row in df.iterrows():
        if pd.isna(row.get('GÜN')) or pd.isna(row.get('SAAT')): continue
        day_str, curr_idx = normalize_day(row['GÜN'])
        if curr_idx == -1: continue
        if prev_idx != -1 and curr_idx < prev_idx: current_week += 1
        prev_idx = curr_idx
        
        d_map = {"PAZARTESI": "Pazartesi", "SALI": "Salı", "CARSAMBA": "Çarşamba", "PERSEMBE": "Perşembe", "CUMA": "Cuma", "CUMARTESI": "Cumartesi", "PAZAR": "Pazar"}
        gun_etiket = f"{d_map[day_str]} ({current_week}. Hafta)"
        
        bas_dk, bit_dk = flexible_time_parse(row['SAAT'])
        if bas_dk is None or bit_dk is None: continue

        rooms = [s.strip() for s in str(row.get('SINAV YERİ', '')).replace(',', '-').split('-') if s.strip()]
        for s in rooms:
            raw_rows.append({
                'Gün': gun_etiket, 'Ders Adı': str(row.get('DERSLER', 'Bilinmeyen Ders')), 
                'Sınav Saati': str(row['SAAT']), 'bas_dk': bas_dk, 'bit_dk': bit_dk, 
                'Sınav Salonu': s, 'Süre': bit_dk - bas_dk, 'Hafta': current_week
            })

    unique_days = []
    for r in raw_rows:
        if r['Gün'] not in unique_days: unique_days.append(r['Gün'])
        
    tasks = []
    max_week_found = max(t['Hafta'] for t in raw_rows) if raw_rows else 1
    
    for d in unique_days:
        day_tasks = [t for t in raw_rows if t['Gün'] == d]
        min_s, max_s = min(t['bas_dk'] for t in day_tasks), max(t['bas_dk'] for t in day_tasks)
        for t in day_tasks:
            t['Mesai Türü'] = 'Normal'
            if t['bas_dk'] == min_s: t['Mesai Türü'] = 'Sabah'
            if max_week_found >= 2:
                if t['bas_dk'] == max_s: t['Mesai Türü'] = 'Akşam'
            elif t['bas_dk'] >= 960: t['Mesai Türü'] = 'Akşam'
            t['slot_id'] = f"{t['Gün']}_{t['bas_dk']}"
            tasks.append(t)
    return tasks, sorted(list(set(t['Sınav Salonu'] for t in tasks))), unique_days

# --- SESSION STATE ---
if 'results' not in st.session_state: st.session_state.results = None
if 'stats' not in st.session_state: st.session_state.stats = None

# --- UI ---
st.sidebar.header("⚙️ Sistem Parametreleri")
uploaded_file = st.sidebar.file_uploader("Sınav Takvimi (Excel)", type=["xlsx", "xls"])
staff_count = st.sidebar.number_input("Toplam Personel Sayısı", min_value=1, value=6)
un_days = st.sidebar.text_area("Günlük Muafiyet No:Gün", placeholder="Örn: 4:Salı (1. Hafta)")
un_times = st.sidebar.text_area("Saatlik Muafiyet No:Saat", placeholder="Örn: 3:16:00-21:00")

st.sidebar.divider()
st.sidebar.header("🎯 İş Yükü Dağılım Stratejileri")
w = {
    "total": st.sidebar.number_input("Toplam Süre Dengesi", 0, 100, 20),
    "big": st.sidebar.number_input("Büyük Salon Dağılımı", 0, 100, 20),
    "morn": st.sidebar.number_input("Sabah Seansı Dengesi", 0, 100, 20),
    "eve": st.sidebar.number_input("Akşam Seansı Dengesi", 0, 100, 20),
    "crit": st.sidebar.number_input("Kritik Seans Dağılımı", 0, 100, 20)
}

if uploaded_file:
    tasks, rooms, days_list = parse_excel(uploaded_file)
    big_rooms = st.sidebar.multiselect("Büyük Salonlar", rooms, default=[r for r in rooms if r in ['301', '303', '304', '309']])
    
    if st.sidebar.button("Optimizasyon Sürecini Başlat"):
        if sum(w.values()) != 100: st.sidebar.error("⚠️ Toplam 100 olmalı.")
        else:
            with st.spinner('Planlama oluşturuluyor...'):
                model = cp_model.CpModel()
                invs = list(range(1, staff_count + 1))
                num_t = len(tasks)
                x = {(i, t): model.NewBoolVar(f'x_{i}_{t}') for i in invs for t in range(num_t)}
                
                restricted_staff = set()
                if un_times:
                    for e in un_times.split(','):
                        if ':' in e: 
                            try: restricted_staff.add(int(e.split(':')[0].strip()))
                            except: pass

                evening_clusters = []
                for i in invs:
                    for slot in set(t['slot_id'] for t in tasks):
                        ov = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                        model.Add(sum(x[i, idx] for idx in ov) <= 1)
                    for d in days_list:
                        day_idx = [idx for idx, t in enumerate(tasks) if t['Gün'] == d]
                        model.Add(sum(x[i, idx] for idx in day_idx) <= 4)
                        eve_tasks_in_day = [idx for idx in day_idx if tasks[idx]['Mesai Türü'] == 'Akşam']
                        if len(eve_tasks_in_day) > 1:
                            h = model.NewBoolVar(f'h_{i}_{d}')
                            model.Add(sum(x[i, idx] for idx in eve_tasks_in_day) >= 2).OnlyEnforceIf(h)
                            evening_clusters.append(h)
                
                for t in range(num_t): model.Add(sum(x[i, t] for i in invs) == 1)

                if un_days:
                    for entry in un_days.split(','):
                        try:
                            s_no, day_raw = entry.split(':')
                            s_no, day_raw = int(s_no.strip()), day_raw.strip().lower()
                            for idx, t in enumerate(tasks):
                                if s_no in invs and day_raw in t['Gün'].lower(): model.Add(x[s_no, idx] == 0)
                        except: pass
                
                if un_times:
                    for entry in un_times.split(','):
                        try:
                            s_no, t_range = entry.split(':', 1)
                            s_no = int(s_no.strip())
                            ex_s, ex_e = to_min(t_range.split('-')[0]), to_min(t_range.split('-')[1])
                            for idx, t in enumerate(tasks):
                                if s_no in invs:
                                    if max(t['bas_dk'], ex_s) < min(t['bit_dk'], ex_e): model.Add(x[s_no, idx] == 0)
                        except: pass

                tm, te, mc, ec, bm = {}, {}, {}, {}, {}
                for i in invs:
                    tm[i] = model.NewIntVar(0, 10000, f'tm_{i}')
                    bm[i] = model.NewIntVar(0, 10000, f'bm_{i}')
                    te[i] = model.NewIntVar(0, 100, f'te_{i}')
                    mc[i] = model.NewIntVar(0, 100, f'mc_{i}')
                    ec[i] = model.NewIntVar(0, 100, f'ec_{i}')
                    model.Add(tm[i] == sum(x[i, t] * tasks[t]['Süre'] for t in range(num_t)))
                    model.Add(bm[i] == sum(x[i, t] * tasks[t]['Süre'] for t in range(num_t) if tasks[t]['Sınav Salonu'] in big_rooms))
                    model.Add(te[i] == sum(x[i, t] for t in range(num_t)))
                    model.Add(mc[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['Mesai Türü'] == 'Sabah'))
                    model.Add(ec[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['Mesai Türü'] == 'Akşam'))

                for var_list in [[te[i] for i in invs], [mc[i] for i in invs]]:
                    mx, mn = model.NewIntVar(0, 100, 'mx'), model.NewIntVar(0, 100, 'mn')
                    model.AddMaxEquality(mx, var_list); model.AddMinEquality(mn, var_list)
                    model.Add(mx - mn <= 2)

                def get_diff(v_dict, subset, name):
                    if not subset: return 0
                    vals = [v_dict[idx] for idx in subset]
                    ma, mi = model.NewIntVar(0, 10000, f'ma_{name}'), model.NewIntVar(0, 10000, f'mi_{name}')
                    model.AddMaxEquality(ma, vals); model.AddMinEquality(mi, vals)
                    d = model.NewIntVar(0, 10000, f'd_{name}'); model.Add(d == ma - mi); return d

                scoring_invs = [i for i in invs if i not in restricted_staff]
                
                model.Minimize(
                    get_diff(tm, invs, "t") * w["total"] * 100 +
                    get_diff(bm, invs, "b") * w["big"] * 100 +
                    get_diff(mc, scoring_invs, "m") * w["morn"] * 1000 + 
                    get_diff(ec, scoring_invs, "e") * w["eve"] * 1000 -
                    sum(evening_clusters) * 8000
                )

                solver = cp_model.CpSolver()
                if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                    st.session_state.results = []
                    for t_idx, t in enumerate(tasks):
                        for i in invs:
                            if solver.Value(x[i, t_idx]):
                                row = t.copy(); row['Görevli Personel'] = i; st.session_state.results.append(row)
                    st.session_state.stats = []
                    for i in invs:
                        st.session_state.stats.append({
                            "Personel": f"{i}{' (Kısıtlı)' if i in restricted_staff else ''}",
                            "Toplam Süre (Dk)": solver.Value(tm[i]), 
                            "Büyük Salon Süresi (Dk)": solver.Value(bm[i]), # Tabloya eklendi
                            "Toplam Görev": solver.Value(te[i]), 
                            "Sabah Seansı": solver.Value(mc[i]), 
                            "Akşam Seansı": solver.Value(ec[i])
                        })
                    st.success("✅ Optimizasyon tamamlandı.")
                else: st.error("❌ Uygun çözüm bulunamadı.")

# --- SONUÇLAR ---
if st.session_state.results:
    res_df = pd.DataFrame(st.session_state.results)
    tab1, tab2, tab3 = st.tabs(["📋 Görev Çizelgesi", "📊 İş Yükü Dağılım Analizi", "📖 Uygulama Metodolojisi"])
    with tab1:
        st.dataframe(res_df[['Gün', 'Ders Adı', 'Sınav Saati', 'Sınav Salonu', 'Görevli Personel']], use_container_width=True)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            res_df[['Gün', 'Ders Adı', 'Sınav Saati', 'Sınav Salonu', 'Görevli Personel']].to_excel(writer, index=False)
        st.download_button("📥 Excel İndir", buffer.getvalue(), "plan.xlsx")
    with tab2: st.table(pd.DataFrame(st.session_state.stats))
    with tab3:
        st.subheader("Sistem Çalışma Prensipleri")
        st.write("Bu yazılım, sınav gözetmenliği planlama sürecini operasyonel verimlilik ve standartlaştırılmış dağılım prensipleri çerçevesinde yürütür.")
        st.info("Sistemin karar mekanizmasında Google tarafından geliştirilen OR-Tools kütüphanesi ve CP-SAT algoritması kullanılmaktadır.")

        st.markdown("### Süreç Analizi ve Hafta Tespiti")
        st.write("""
        Sistem, yüklenen sınav takvimini satır satır tarayarak kronolojik bir zaman çizelgesi oluşturur. Günlerin takvim akışı incelenerek programın bir veya birden fazla haftadan oluştuğu otomatik olarak saptanır. Muafiyet tanımları yapılırken 'Salı (1. Hafta)' gibi ifadeler kullanılarak, on günlük süreçteki tekil günler spesifik olarak kısıtlanabilmektedir.
        """)

        st.markdown("### Seans Sınıflandırma Mantığı")
        st.write("""
        Sınavlar, başlangıç saatlerine ve programın toplam süresine göre dinamik olarak etiketlenir:
        - **Sabah Seansı:** Her takvim gününün gerçekleşen ilk sınavı, o günün açılış görevi olarak işaretlenir.
        - **Akşam Seansı:** Tanım, programın hafta sayısına göre değişir. Tek haftalık programlarda saat 16:00 ve sonrasında başlayan tüm sınavlar 'Akşam' kabul edilirken; iki haftalık programlarda personeli korumak amacıyla sadece günün en son sınav saati 'Akşam' olarak kabul edilir.
        """)

        st.markdown("### Operasyonel Görev Kuralları")
        st.write("""
        Sistem, aşağıdaki operasyonel kriterleri her zaman korur:
        - **Zaman Çakışması Kontrolü:** Bir personelin aynı zaman diliminde birden fazla salonda bulunması matematiksel olarak engellenir.
        - **Günlük Görev Sınırı:** Operasyonel sürekliliği sağlamak adına, bir personelin bir takvim günü içindeki maksimum görev sayısı dörttür.
        - **Dağılım Dengesi:** Program boyunca en çok ve en az görev alan personeller arasındaki farkın ikiden fazla olmasına izin verilmez. Bu denge kuralı sabah seansları için de ayrıca işletilir.
        """)

        st.markdown("### Kümelenme Stratejisi ve Verimlilik")
        st.write("""
        Sistem, personelin kampüs içindeki zamanını optimize etmek için **Kümelenme Stratejisi** uygular. Eğer bir personel o gün akşam seansına atanmışsa, sistem o personeli (çakışma yoksa) o günkü diğer akşam sınavlarına da atamaya yüksek öncelik verir. 
        Özellikle tek haftalık programlarda 16:00 sonrasındaki tüm sınavların akşam sayılması, bu kümelenme etkisini artırır. Böylece bir veya iki personelin akşam yükünü üstlenerek görevlerini tamamlaması sağlanırken, diğer personellerin akşam mesaisine kalmadan operasyonel süreçten çıkmalarına olanak tanınır.
        """)

        st.markdown("### Algoritmik Karar Mekanizması")
        st.write("""
        Google CP-SAT (Constraint Programming - Satisfiability) çözücüsü, tanımlanan tüm kısıtlamaları (±2 denge kuralları, muafiyetler, süreler) saniyeler içerisinde milyarlarca olasılık arasından tarar. Algoritma, tüm 'Sert Kısıtları' kesin olarak sağlarken, kullanıcı tarafından belirlenen stratejik ağırlıkları (Toplam Süre, Büyük Salon vb.) en verimli hale getirecek çözümü üretir.
        """)
