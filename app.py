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
    for d in unique_days:
        day_tasks = [t for t in raw_rows if t['Gün'] == d]
        min_s, max_s = min(t['bas_dk'] for t in day_tasks), max(t['bas_dk'] for t in day_tasks)
        for t in day_tasks:
            t['Mesai Türü'] = 'Normal'
            if t['bas_dk'] == min_s: t['Mesai Türü'] = 'Sabah'
            if current_week >= 2:
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
un_days = st.sidebar.text_area("Günlük Muafiyet (No:Gün)", placeholder="4:Salı (1. Hafta)")
un_times = st.sidebar.text_area("Saatlik Muafiyet (No:SaatAralığı)", placeholder="3:16:00-21:00")

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

                # --- KISITLAR ---
                for i in invs:
                    for slot in set(t['slot_id'] for t in tasks):
                        ov = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                        model.Add(sum(x[i, idx] for idx in ov) <= 1)
                    for d in days_list:
                        day_idx = [idx for idx, t in enumerate(tasks) if t['Gün'] == d]
                        model.Add(sum(x[i, idx] for idx in day_idx) <= 4)
                
                for t in range(num_t): model.Add(sum(x[i, t] for i in invs) == 1)

                # Muafiyet Uygulamaları
                if un_days:
                    for entry in un_days.split(','):
                        try:
                            s_no, day_raw = entry.split(':')
                            s_no = int(s_no.strip()); day_raw = day_raw.strip().lower()
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

                # --- İSTATİSTİKLER VE SERT DENGELEME ---
                total_mins, total_exams, morn_cnt, eve_cnt, big_mins = {}, {}, {}, {}, {}
                for i in invs:
                    total_mins[i] = model.NewIntVar(0, 10000, f'tm_{i}')
                    big_mins[i] = model.NewIntVar(0, 10000, f'bm_{i}')
                    total_exams[i] = model.NewIntVar(0, 100, f'te_{i}')
                    morn_cnt[i] = model.NewIntVar(0, 100, f'mc_{i}')
                    eve_cnt[i] = model.NewIntVar(0, 100, f'ec_{i}')
                    
                    model.Add(total_mins[i] == sum(x[i, t] * tasks[t]['Süre'] for t in range(num_t)))
                    model.Add(big_mins[i] == sum(x[i, t] * tasks[t]['Süre'] for t in range(num_t) if tasks[t]['Sınav Salonu'] in big_rooms))
                    model.Add(total_exams[i] == sum(x[i, t] for t in range(num_t)))
                    model.Add(morn_cnt[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['Mesai Türü'] == 'Sabah'))
                    model.Add(eve_cnt[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['Mesai Türü'] == 'Akşam'))

                # 1. SERT KURAL: Toplam Görev Sayısı Dengesi (±2)
                max_te, min_te = model.NewIntVar(0, 100, 'max_te'), model.NewIntVar(0, 100, 'min_te')
                model.AddMaxEquality(max_te, [total_exams[i] for i in invs])
                model.AddMinEquality(min_te, [total_exams[i] for i in invs])
                model.Add(max_te - min_te <= 2)

                # 2. SERT KURAL: Sabah Seansı Sayısı Dengesi (±2)
                # Bu kural, kısıtlı personelin üzerine sabah yükü yığılmasını engeller.
                max_mc, min_mc = model.NewIntVar(0, 100, 'max_mc'), model.NewIntVar(0, 100, 'min_mc')
                model.AddMaxEquality(max_mc, [morn_cnt[i] for i in invs])
                model.AddMinEquality(min_mc, [morn_cnt[i] for i in invs])
                model.Add(max_mc - min_mc <= 2)

                def get_diff(v_dict, subset, name):
                    if not subset: return 0
                    vals = [v_dict[idx] for idx in subset]
                    ma, mi = model.NewIntVar(0, 10000, f'ma_{name}'), model.NewIntVar(0, 10000, f'mi_{name}')
                    model.AddMaxEquality(ma, vals); model.AddMinEquality(mi, vals)
                    d = model.NewIntVar(0, 10000, f'd_{name}'); model.Add(d == ma - mi); return d

                scoring_invs = [i for i in invs if i not in restricted_staff]
                model.Minimize(
                    get_diff(total_mins, invs, "t") * w["total"] * 100 +
                    get_diff(big_mins, invs, "b") * w["big"] * 100 +
                    get_diff(morn_cnt, scoring_invs, "m") * w["morn"] * 1000 + 
                    get_diff(eve_cnt, scoring_invs, "e") * w["eve"] * 1000
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
                            "Toplam Süre (Dk)": solver.Value(total_mins[i]), 
                            "Büyük Salon Süresi": solver.Value(big_mins[i]),
                            "Toplam Görev Sayısı": solver.Value(total_exams[i]), 
                            "Sabah Seansı Sayısı": solver.Value(morn_cnt[i]),
                            "Akşam Seansı Sayısı": solver.Value(eve_cnt[i])
                        })
                    st.success("✅ Operasyonel görev planlaması başarıyla tamamlanmıştır.")
                else: st.error("❌ Belirlenen kriterler dahilinde uygun bir plan bulunamadı. Lütfen kısıtlamaları kontrol edin.")

# --- SONUÇLAR ---
if st.session_state.results:
    res_df = pd.DataFrame(st.session_state.results)
    tab1, tab2, tab3 = st.tabs(["📋 Görev Çizelgesi", "📊 İş Yükü Dağılım Analizi", "📖 Uygulama Metodolojisi"])
    with tab1:
        st.dataframe(res_df[['Gün', 'Ders Adı', 'Sınav Saati', 'Sınav Salonu', 'Görevli Personel']], use_container_width=True)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            res_df[['Gün', 'Ders Adı', 'Sınav Saati', 'Sınav Salonu', 'Görevli Personel']].to_excel(writer, index=False)
        st.download_button("📥 Çizelgeyi Excel İndir", buffer.getvalue(), "plan.xlsx")
    with tab2: st.table(pd.DataFrame(st.session_state.stats))
    with tab3:
        st.subheader("Sistem Çalışma Prensipleri")
        st.write("Bu yazılım, sınav gözetmenliği planlama sürecini operasyonel verimlilik ve standartlaştırılmış dağılım prensipleri çerçevesinde yürütür.")
        st.info("Bu sistemin karar verme mekanizmasında Google tarafından geliştirilen OR-Tools kütüphanesi ve CP-SAT algoritması kullanılmıştır.")

        st.markdown("### Süreç Analizi ve Dönem Tespiti")
        st.write("Sistem, günlerin takvim akışına göre hafta geçişlerini otomatik belirler. Her takvim gününün başlayan ilk sınavı 'Sabah Seansı' olarak tanımlanır.")

        st.markdown("### Operasyonel Standartlar ve Sert Kısıtlar")
        st.write("""
        - **Zaman Çakışması Kontrolü:** Bir personel aynı anda iki yerde olamaz.
        - **Görev Sayısı Dengesi:** Programın tamamında en çok ve en az görev alan arasındaki fark ikiden fazla olamaz.
        - **Seans Bazlı Denge:** Sabah seansı görevleri tüm personele eşit dağıtılmak zorundadır (Fark en fazla 2 olabilir).
        - **Muafiyet Yönetimi:** Girilen tüm kısıtlamalar en öncelikli kural olarak işlenir.
        """)
