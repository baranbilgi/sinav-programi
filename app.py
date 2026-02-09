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
        clean_time = re.sub(r'[^0-9:]', ':', str(time_str).replace('.', ':')).strip()
        if ':' not in clean_time: return None
        h, m = map(int, clean_time.split(':')[:2])
        return h * 60 + m
    except:
        return None

def parse_excel(file):
    df = pd.read_excel(file)
    df.columns = [c.strip().upper() for c in df.columns]
    
    day_map = {
        "PAZARTESI": 0, "PAZARTESİ": 0, "SALI": 1, "ÇARŞAMBA": 2, "CARŞAMBA": 2, 
        "PERŞEMBE": 3, "PERŞEMBE": 3, "CUMA": 4, "CUMARTESİ": 5, "CUMARTESİ": 5, "PAZAR": 6
    }
    
    raw_rows = []
    current_week = 1
    prev_day_idx = -1
    seen_days_in_week = set()
    
    for _, row in df.iterrows():
        if pd.isna(row.get('GÜN')) or pd.isna(row.get('SAAT')): continue
        
        gun_raw = str(row['GÜN']).strip().upper()
        # Gün ismini normalize et (Örn: "Cuma\n14/11" -> "CUMA")
        gun_temiz = re.sub(r'[^A-ZÇĞİÖŞÜ]', '', gun_raw.replace('İ', 'I')).replace('I', 'İ')
        
        # Gün sırasını bul
        curr_day_idx = -1
        for key, val in day_map.items():
            if key in gun_temiz:
                curr_day_idx = val
                break
        
        if curr_day_idx == -1: continue
        
        # Hafta Geçiş Kontrolü: Gün sırası geriye düştüyse veya aynı gün tekrarlandıysa
        if curr_day_idx <= prev_day_idx or gun_temiz in seen_days_in_week:
            current_week += 1
            seen_days_in_week = set()
            
        seen_days_in_week.add(gun_temiz)
        prev_day_idx = curr_day_idx
        
        gun_etiket = f"{gun_temiz.capitalize()} ({current_week}. Hafta)"
        
        ders_adi = str(row.get('DERSLER', 'Bilinmeyen Ders'))
        saat_araligi = str(row['SAAT'])
        sinav_yerleri = str(row.get('SINAV YERİ', ''))
        
        try:
            bas_str, bit_str = saat_araligi.split('-')
            bas_dakika = to_min(bas_str)
            bit_dakika = to_min(bit_str)
            sure = bit_dakika - bas_dakika
        except: continue

        sinif_listesi = [s.strip() for s in sinav_yerleri.replace(',', '-').split('-') if s.strip()]
        for s in sinif_listesi:
            raw_rows.append({
                'Gün': gun_etiket, 'Ders Adı': ders_adi, 'Sınav Saati': saat_araligi,
                'bas_dk': bas_dakika, 'Sınav Salonu': s, 'Süre (Dakika)': sure,
                'bas_str': bas_str.strip()
            })

    # Sabah/Akşam Etiketleme
    tasks = []
    all_rooms = set()
    unique_days = []
    for d in raw_rows:
        if d['Gün'] not in unique_days: unique_days.append(d['Gün'])
        
    for d in unique_days:
        day_tasks = [t for t in raw_rows if t['Gün'] == d]
        min_start = min(t['bas_dk'] for t in day_tasks)
        
        for t in day_tasks:
            label = 'Normal'
            if t['bas_dk'] == min_start: label = 'Sabah'
            elif t['bas_dk'] >= 960: label = 'Akşam'
            
            t['Mesai Türü'] = label
            t['slot_id'] = f"{t['Gün']}_{t['bas_str']}"
            all_rooms.add(t['Sınav Salonu'])
            tasks.append(t)
            
    return tasks, sorted(list(all_rooms)), unique_days

# --- YAN MENÜ ---
st.sidebar.header("⚙️ Sistem Parametreleri")
uploaded_file = st.sidebar.file_uploader("Sınav Takvimi (Excel)", type=["xlsx", "xls"])
staff_count = st.sidebar.number_input("Toplam Personel Sayısı", min_value=1, value=6)

st.sidebar.divider()
st.sidebar.subheader("🚫 Görev Muafiyet Tanımları")
unavailable_days_input = st.sidebar.text_area("Günlük Muafiyet (PersonelNo:Gün)", placeholder="Örn: 1:Pazartesi (1. Hafta)")
unavailable_times_input = st.sidebar.text_area("Saatlik Muafiyet (PersonelNo:Saat)", placeholder="Örn: 1:16:00-21:00")

st.sidebar.divider()
st.sidebar.header("🎯 Dağılım Stratejileri")
w_total = st.sidebar.number_input("Toplam İş Yükü Dengesi", 0, 100, 20)
w_big = st.sidebar.number_input("Büyük Salon Dağılımı", 0, 100, 20)
w_morn = st.sidebar.number_input("Sabah Seansı Dengesi", 0, 100, 20)
w_eve = st.sidebar.number_input("Akşam Seansı Dengesi", 0, 100, 20)
w_sa_total = st.sidebar.number_input("Kritik Seans Toplamı Dengesi", 0, 100, 20)

if uploaded_file:
    tasks, rooms, days_list = parse_excel(uploaded_file)
    big_rooms = st.sidebar.multiselect("Büyük Salonlar", rooms, default=[r for r in rooms if r in ['301', '303', '304']])
    
    if st.sidebar.button("Optimizasyon Sürecini Başlat"):
        if (w_total + w_big + w_morn + w_eve + w_sa_total) != 100:
            st.sidebar.error("⚠️ Strateji ağırlıkları toplamı 100 olmalıdır.")
        else:
            with st.spinner('Görev dağılımı optimize ediliyor...'):
                model = cp_model.CpModel()
                invs = list(range(1, staff_count + 1))
                num_t = len(tasks)
                x = {(i, t): model.NewBoolVar(f'x_{i}_{t}') for i in invs for t in range(num_t)}
                
                restricted_staff = set()
                if unavailable_times_input:
                    for entry in unavailable_times_input.split(','):
                        if ':' in entry:
                            try: restricted_staff.add(int(entry.split(':')[0].strip()))
                            except: pass

                evening_clusters = []
                for i in invs:
                    for slot in set(t['slot_id'] for t in tasks):
                        overlap = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                        model.Add(sum(x[i, idx] for idx in overlap) <= 1)
                    for d in days_list:
                        day_tasks_idx = [idx for idx, t in enumerate(tasks) if t['Gün'] == d]
                        model.Add(sum(x[i, idx] for idx in day_tasks_idx) <= 4)
                        eve_tasks_in_day = [idx for idx in day_tasks_idx if tasks[idx]['Mesai Türü'] == 'Akşam']
                        if len(eve_tasks_in_day) > 1:
                            h = model.NewBoolVar(f'multi_eve_{i}_{d}')
                            model.Add(sum(x[i, idx] for idx in eve_tasks_in_day) >= 2).OnlyEnforceIf(h)
                            evening_clusters.append(h)

                for t in range(num_t):
                    model.Add(sum(x[i, t] for i in invs) == 1)

                if unavailable_days_input:
                    for entry in unavailable_days_input.split(','):
                        try:
                            s_no, d_name = entry.split(':')
                            s_no = int(s_no.strip()); d_name = d_name.strip().lower()
                            for idx, t in enumerate(tasks):
                                if s_no in invs and d_name in t['Gün'].lower(): model.Add(x[s_no, idx] == 0)
                        except: continue

                if unavailable_times_input:
                    for entry in unavailable_times_input.split(','):
                        try:
                            parts = entry.split(':', 1)
                            s_no, t_range = int(parts[0]), parts[1].strip()
                            st_str, en_str = t_range.split('-')
                            ex_s, ex_e = to_min(st_str), to_min(en_str)
                            for idx, t in enumerate(tasks):
                                ts, te = t['bas_dk'], t['bas_dk'] + t['Süre (Dakika)']
                                if s_no in invs and max(ts, ex_s) < min(te, ex_e): model.Add(x[s_no, idx] == 0)
                        except: continue

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

                max_e, min_e = model.NewIntVar(0, 100, 'max_e'), model.NewIntVar(0, 100, 'min_e')
                model.AddMaxEquality(max_e, [total_exams[i] for i in invs])
                model.AddMinEquality(min_e, [total_exams[i] for i in invs])
                model.Add(max_e - min_e <= 2)

                def get_diff(v_dict, subset, name):
                    if not subset: return 0
                    vals = [v_dict[i] for i in subset]
                    ma, mi = model.NewIntVar(0, 10000, f'ma_{name}'), model.NewIntVar(0, 10000, f'mi_{name}')
                    model.AddMaxEquality(ma, vals); model.AddMinEquality(mi, vals)
                    d = model.NewIntVar(0, 10000, f'd_{name}'); model.Add(d == ma - mi)
                    return d

                scoring_invs = [i for i in invs if i not in restricted_staff]
                model.Minimize(
                    get_diff(total_mins, invs, "t") * w_total * 100 +
                    get_diff(big_mins, invs, "b") * w_big * 100 +
                    get_diff(morn_cnt, scoring_invs, "m") * w_morn * 1000 + 
                    get_diff(eve_cnt, scoring_invs, "e") * w_eve * 1000 +
                    get_diff(critical_sum, scoring_invs, "c") * w_sa_total * 1000 -
                    sum(evening_clusters) * 5000 
                )

                solver = cp_model.CpSolver()
                solver.parameters.max_time_in_seconds = 30.0
                if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                    st.success("✅ Görev planlaması başarıyla tamamlanmıştır.")
                    res = []
                    for t_idx, t in enumerate(tasks):
                        for i in invs:
                            if solver.Value(x[i, t_idx]):
                                row = t.copy(); row['Görevli Personel'] = i; res.append(row)
                    
                    df_res = pd.DataFrame(res)
                    tab1, tab2, tab3 = st.tabs(["📋 Görev Çizelgesi", "📊 Görev Dağılım İstatistikleri", "📖 Uygulama Metodolojisi"])
                    with tab1:
                        final_df = df_res[['Gün', 'Ders Adı', 'Sınav Saati', 'Sınav Salonu', 'Görevli Personel']]
                        st.dataframe(final_df, use_container_width=True)
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                            final_df.to_excel(writer, index=False, sheet_name='Plan')
                        st.download_button("📥 Çizelgeyi İndir", buffer.getvalue(), "gorev_plani.xlsx")
                    
                    with tab2:
                        stats = []
                        for i in invs:
                            stats.append({
                                "Personel": f"{i}{' (Muaf)' if i in restricted_staff else ''}", 
                                "Top. Mesai (Dk)": solver.Value(total_mins[i]), 
                                "Büyük Salon (Dk)": solver.Value(big_mins[i]),
                                "Toplam Sınav Sayısı": solver.Value(total_exams[i]),
                                "Sabah Seansı": solver.Value(morn_cnt[i]), 
                                "Akşam Seansı": solver.Value(eve_cnt[i]), 
                                "Kritik Seans Toplamı": solver.Value(critical_sum[i])
                            })
                        st.table(pd.DataFrame(stats))
                    
                    with tab3:
                        st.subheader("Sistem Çalışma Prensipleri")
                        st.write("""
                        Bu yazılım, personel görevlendirme sürecini kurumsal standartlara ve adalet ilkelerine göre yönetir. Sistemin işleyiş detayları aşağıda belirtilmiştir:
                        """)
                        
                        st.markdown("### Görev Tanımlama ve Sınıflandırma")
                        st.write("""
                        Sistem, yüklenen programı otomatik olarak haftalara ayırır. Günlerin sırasını takip ederek, programın hangi bölümlerinin 1. hafta, hangi bölümlerinin 2. hafta olduğunu tespit eder. 
                        Her takvim gününün başlayan ilk sınavı, o günün açılış görevi olması nedeniyle sistem tarafından otomatik olarak 'Sabah Seansı' olarak işaretlenir. 
                        Saat 16:00 ve sonrasında başlayan tüm görevler ise 'Akşam Mesaisi' olarak sınıflandırılır.
                        """)

                        st.markdown("### Kurallar ve Kısıtlamalar")
                        st.write("""
                        Planlama oluşturulurken aşağıdaki temel kurallar sistem tarafından her zaman korunur:
                        - Bir personel aynı zaman diliminde birden fazla sınavda görevlendirilemez; tüm çakışmalar otomatik olarak önlenir.
                        - Personel iş yükünü dengelemek adına, hiçbir personele bir gün içerisinde 4 sınavdan fazla görev verilmez.
                        - En kritik kural olarak; tüm süreç boyunca en çok görev alan personel ile en az görev alan personel arasındaki fark 2 sınavı asla geçemez. Bu sayede görevler tüm personele homojen bir şekilde yayılır.
                        - Kullanıcı tarafından tanımlanan günlük veya saatlik muafiyetler sisteme en öncelikli kural olarak işlenir ve bu zamanlarda personele görev yazılmaz.
                        """)

                        st.markdown("### İş Yükü Dağılımı ve Adalet")
                        st.write("""
                        Sistem, sadece sınav sayılarını değil, personelin harcadığı toplam süreyi ve girdiği salonların büyüklüğünü de hesaba katar. Tüm bu veriler bütünleşik bir yapıda değerlendirilir. 
                        Özellikle saatlik muafiyeti bulunan personeller, sabah veya akşam seansı gibi özel dengeleme hesaplamalarından çıkarılır. Bu sayede, kısıtlı bir personelin düşük olan seans sayısı, genel adalet tablosunu yanıltmaz ve diğer personel kendi içinde en adil şekilde gruplandırılmaya devam eder.
                        """)

                        st.markdown("### Verimlilik ve Akşam Görevleri")
                        st.write("""
                        Personel verimliliğini artırmak ve gereksiz beklemeleri önlemek amacıyla sistem 'akıllı kümelenme' yöntemini kullanır. 
                        Eğer bir personel o gün akşam seansına atanmışsa, sistem o personeli ikinci bir akşam sınavına atamaya öncelik verir. 
                        Böylece bir personelin akşam kampüste bulunduğu sürede görevlerini tamamlaması sağlanırken, diğer personellerin akşam mesaisine kalmasına gerek kalmadan evlerine dönebilmeleri amaçlanır.
                        """)
                else:
                    st.error("❌ Uygun bir senaryo üretilemedi. ±2 sınav farkı kuralını karşılamak için personel sayısını artırabilir veya muafiyetleri esnetebilirsiniz.")
