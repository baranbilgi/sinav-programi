import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import io
import re

# Sayfa Yapılandırması
st.set_page_config(page_title="Gözetmen Planlama Sistemi", layout="wide")
st.title("🏛️ Gözetmen Optimizasyon ve Görev Planlama Sistemi")

# --- YARDIMCI FONKSİYONLAR ---
def to_min(time_str):
    if not time_str or pd.isna(time_str): return None
    try:
        # Saatleri temizle (12:15, 12.15, 12:15:00 vb.)
        clean_time = re.sub(r'[^0-9:]', ':', str(time_str).replace('.', ':')).strip()
        parts = clean_time.split(':')
        h, m = int(parts[0]), int(parts[1])
        return h * 60 + m
    except:
        return None

def normalize_day(text):
    if pd.isna(text): return None, -1
    text = str(text).upper()
    mapping = {"İ": "I", "I": "I", "Ş": "S", "Ğ": "G", "Ü": "U", "Ö": "O", "Ç": "C"}
    for k, v in mapping.items():
        text = text.replace(k, v)
    text = re.sub(r'[^A-Z]', '', text)
    check_map = {"PAZARTESI": 0, "SALI": 1, "CARSAMBA": 2, "PERSEMBE": 3, "CUMA": 4, "CUMARTESI": 5, "PAZAR": 6}
    for day_key, day_idx in check_map.items():
        if day_key in text: return day_key, day_idx
    return None, -1

def parse_excel(file):
    df = pd.read_excel(file)
    df.columns = [c.strip().upper() for c in df.columns]
    raw_rows, current_week, prev_day_idx = [], 1, -1
    
    for _, row in df.iterrows():
        if pd.isna(row.get('GÜN')) or pd.isna(row.get('SAAT')): continue
        day_str, curr_day_idx = normalize_day(row['GÜN'])
        if curr_day_idx == -1: continue
        if prev_day_idx != -1 and curr_day_idx < prev_day_idx: current_week += 1
        prev_day_idx = curr_day_idx
        
        display_map = {"PAZARTESI": "Pazartesi", "SALI": "Salı", "CARSAMBA": "Çarşamba", "PERSEMBE": "Perşembe", "CUMA": "Cuma", "CUMARTESI": "Cumartesi", "PAZAR": "Pazar"}
        gun_etiket = f"{display_map[day_str]} ({current_week}. Hafta)"
        
        try:
            parts = str(row['SAAT']).split('-')
            bas_dk, bit_dk = to_min(parts[0]), to_min(parts[1])
            sure = bit_dk - bas_dk
        except: continue

        rooms = [s.strip() for s in str(row.get('SINAV YERİ', '')).replace(',', '-').split('-') if s.strip()]
        for s in rooms:
            raw_rows.append({
                'Gün': gun_etiket, 'Ders Adı': str(row.get('DERSLER', 'Bilinmeyen Ders')), 
                'Sınav Saati': str(row['SAAT']), 'bas_dk': bas_dk, 'bit_dk': bit_dk, 
                'Sınav Salonu': s, 'Süre (Dakika)': sure, 'Hafta': current_week
            })

    max_week = current_week
    tasks, unique_days = [], []
    for r in raw_rows:
        if r['Gün'] not in unique_days: unique_days.append(r['Gün'])
        
    for d in unique_days:
        day_tasks = [t for t in raw_rows if t['Gün'] == d]
        min_start, max_start = min(t['bas_dk'] for t in day_tasks), max(t['bas_dk'] for t in day_tasks)
        for t in day_tasks:
            label = 'Normal'
            if t['bas_dk'] == min_start: label = 'Sabah'
            if max_week >= 2:
                if t['bas_dk'] == max_start: label = 'Akşam'
            else:
                if t['bas_dk'] >= 960: label = 'Akşam'
            t['Mesai Türü'] = label
            t['slot_id'] = f"{t['Gün']}_{t['bas_dk']}"
            tasks.append(t)
    return tasks, sorted(list(set(t['Sınav Salonu'] for t in tasks))), unique_days

# --- SESSION STATE ---
if 'results' not in st.session_state: st.session_state.results = None
if 'stats' not in st.session_state: st.session_state.stats = None

# --- YAN MENÜ ---
st.sidebar.header("⚙️ Sistem Parametreleri")
uploaded_file = st.sidebar.file_uploader("Sınav Takvimi (Excel)", type=["xlsx", "xls"])
staff_count = st.sidebar.number_input("Toplam Personel Sayısı", min_value=1, value=6)
st.sidebar.divider()
st.sidebar.subheader("🚫 Görev Muafiyet Tanımları")
un_days = st.sidebar.text_area("Günlük Muafiyet (No:Gün)", placeholder="1:Pazartesi (1. Hafta)")
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
    big_rooms = st.sidebar.multiselect("Büyük Salonlar", rooms, default=[r for r in rooms if r in ['301', '303', '304']])
    
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

                # --- MUAFİYETLER (SERT KISIT) ---
                if un_days:
                    for entry in un_days.split(','):
                        try:
                            s_no, d_name = entry.split(':')
                            s_no = int(s_no.strip()); d_name = d_name.strip().lower()
                            for idx, t in enumerate(tasks):
                                if s_no in invs and d_name in t['Gün'].lower(): model.Add(x[s_no, idx] == 0)
                        except: pass
                
                if un_times:
                    for entry in un_times.split(','):
                        try:
                            parts = entry.split(':', 1)
                            s_no, t_range = int(parts[0]), parts[1].strip()
                            r_pts = t_range.split('-')
                            ex_s, ex_e = to_min(r_pts[0]), to_min(r_pts[1])
                            for idx, t in enumerate(tasks):
                                if s_no in invs:
                                    # Sınavın herhangi bir anı muafiyet aralığına giriyorsa atama yapma
                                    if max(t['bas_dk'], ex_s) < min(t['bit_dk'], ex_e):
                                        model.Add(x[s_no, idx] == 0)
                        except: pass

                # --- İSTATİSTİKLER ---
                total_mins, big_mins, total_exams, morn_cnt, eve_cnt, critical_sum = {}, {}, {}, {}, {}, {}
                for i in invs:
                    total_mins[i] = model.NewIntVar(0, 10000, f'tm_{i}')
                    big_mins[i] = model.NewIntVar(0, 10000, f'bm_{i}')
                    total_exams[i] = model.NewIntVar(0, 100, f'te_{i}')
                    morn_cnt[i] = model.NewIntVar(0, 100, f'mc_{i}')
                    eve_cnt[i] = model.NewIntVar(0, 100, f'ec_{i}')
                    critical_sum[i] = model.NewIntVar(0, 200, f'cs_{i}')
                    
                    model.Add(total_mins[i] == sum(x[i, t] * tasks[t]['Süre (Dakika)'] for t in range(num_t)))
                    model.Add(big_mins[i] == sum(x[i, t] * tasks[t]['Süre (Dakika)'] for t in range(num_t) if tasks[t]['Sınav Salonu'] in big_rooms))
                    model.Add(total_exams[i] == sum(x[i, t] for t in range(num_t)))
                    model.Add(morn_cnt[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['Mesai Türü'] == 'Sabah'))
                    model.Add(eve_cnt[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['Mesai Türü'] == 'Akşam'))
                    model.Add(critical_sum[i] == morn_cnt[i] + eve_cnt[i])

                # ±2 Fark Kısıtı
                max_e, min_e = model.NewIntVar(0, 100, 'max_e'), model.NewIntVar(0, 100, 'min_e')
                model.AddMaxEquality(max_e, [total_exams[i] for i in invs])
                model.AddMinEquality(min_e, [total_exams[i] for i in invs])
                model.Add(max_e - min_e <= 2)

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
                    get_diff(eve_cnt, scoring_invs, "e") * w["eve"] * 1000 +
                    get_diff(critical_sum, scoring_invs, "c") * w["crit"] * 1000
                )

                solver = cp_model.CpSolver()
                if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                    st.session_state.results = []
                    for t_idx, t in enumerate(tasks):
                        for i in invs:
                            if solver.Value(x[i, t_idx]):
                                row = t.copy(); row['Görevli Personel'] = i
                                st.session_state.results.append(row)
                    st.session_state.stats = []
                    for i in invs:
                        st.session_state.stats.append({
                            "Personel": f"{i}{' (Kısıtlı)' if i in restricted_staff else ''}",
                            "Toplam Süre (Dk)": solver.Value(total_mins[i]), 
                            "Büyük Salon Süresi": solver.Value(big_mins[i]),
                            "Toplam Görev Sayısı": solver.Value(total_exams[i]), 
                            "Sabah Seansı Sayısı": solver.Value(morn_cnt[i]),
                            "Akşam Seansı Sayısı": solver.Value(eve_cnt[i]), 
                            "Kritik Seans Toplamı": solver.Value(critical_sum[i])
                        })
                    st.success("✅ İşlem tamamlandı.")
                else: st.error("❌ Uygun plan bulunamadı.")

# --- SONUÇ GÖSTERİMİ ---
if st.session_state.results:
    res_df = pd.DataFrame(st.session_state.results)
    tab1, tab2, tab3 = st.tabs(["📋 Görev Çizelgesi", "📊 İş Yükü Dağılım Analizi", "📖 Uygulama Metodolojisi"])
    with tab1:
        st.dataframe(res_df[['Gün', 'Ders Adı', 'Sınav Saati', 'Sınav Salonu', 'Görevli Personel']], use_container_width=True)
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
            res_df[['Gün', 'Ders Adı', 'Sınav Saati', 'Sınav Salonu', 'Görevli Personel']].to_excel(writer, index=False)
        st.download_button("📥 Excel İndir", buffer.getvalue(), "plan.xlsx", key="final_dl")
    with tab2: st.table(pd.DataFrame(st.session_state.stats))
    with tab3:
        st.subheader("Sistem Çalışma Prensipleri")
        st.write("Bu yazılım, operasyonel verimlilik ve standartlaştırılmış dağılım ilkeleriyle çalışır.")
        st.markdown("### Süreç Analizi ve Dönem Tespiti")
        st.write("Sistem hafta geçişlerini otomatik belirler. Günün ilk sınavı 'Sabah', program uzunluğuna göre günün sonu veya saat 16:00 sonrası 'Akşam' olarak sınıflandırılır.")
        st.markdown("### Operasyonel Standartlar")
        st.write("- Çakışmalar engellenir.\n- Günlük görev sınırı dörttür.\n- Görev farkı en fazla ikidir.\n- Muafiyetler öncelikli kısıttır; kısıtlanan saatlerde personele kesinlikle görev atanmaz.")
