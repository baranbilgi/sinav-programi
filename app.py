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
        "PERŞEMBE": 3, "PERŞEMBE": 3, "CUMA": 4, "CUMARTESİ": 5, "PAZAR": 6
    }
    
    raw_rows = []
    current_week = 1
    prev_day_idx = -1
    
    # İlk geçiş: Ham veriyi oku ve hafta bilgisini belirle
    for _, row in df.iterrows():
        if pd.isna(row.get('GÜN')) or pd.isna(row.get('SAAT')): continue
        
        gun_raw = str(row['GÜN']).strip().upper()
        gun_temiz = re.sub(r'[^A-ZÇĞİÖŞÜ]', '', gun_raw.replace('İ', 'I')).replace('I', 'İ')
        
        curr_day_idx = -1
        for key, val in day_map.items():
            if key in gun_temiz:
                curr_day_idx = val
                break
        
        if curr_day_idx == -1: continue
        
        if prev_day_idx != -1 and curr_day_idx < prev_day_idx:
            current_week += 1
            
        prev_day_idx = curr_day_idx
        gun_etiket = f"{gun_temiz.capitalize()} ({current_week}. Hafta)"
        
        ders_adi = str(row.get('DERSLER', 'Bilinmeyen Ders'))
        saat_araligi = str(row['SAAT'])
        sinav_yerleri = str(row.get('SINAV YERİ', ''))
        
        try:
            bas_str, bit_str = saat_araligi.split('-')
            bas_dk = to_min(bas_str)
            bit_dk = to_min(bit_str)
            sure = bit_dk - bas_dk
        except: continue

        sinif_listesi = [s.strip() for s in sinav_yerleri.replace(',', '-').split('-') if s.strip()]
        for s in sinif_listesi:
            raw_rows.append({
                'Gün': gun_etiket, 'Ders Adı': ders_adi, 'Sınav Saati': saat_araligi,
                'bas_dk': bas_dk, 'Sınav Salonu': s, 'Süre (Dakika)': sure,
                'bas_str': bas_str.strip(), 'Hafta': current_week
            })

    # Toplam hafta sayısını belirle
    max_week = current_week
    
    # İkinci geçiş: Seans etiketleme
    tasks = []
    all_rooms = set()
    unique_days = []
    for r in raw_rows:
        if r['Gün'] not in unique_days: unique_days.append(r['Gün'])
        
    for d in unique_days:
        day_tasks = [t for t in raw_rows if t['Gün'] == d]
        if not day_tasks: continue
        
        min_start = min(t['bas_dk'] for t in day_tasks)
        max_start = max(t['bas_dk'] for t in day_tasks)
        
        for t in day_tasks:
            label = 'Normal'
            # Sabah Tanımı: Her zaman günün ilk sınavı
            if t['bas_dk'] == min_start:
                label = 'Sabah'
            # Akşam Tanımı: Hafta sayısına göre değişir
            if max_week >= 2:
                # 2 haftalık program: Günün son sınavı
                if t['bas_dk'] == max_start: label = 'Akşam'
            else:
                # Tek haftalık program: 16:00 kuralı (960 dk)
                if t['bas_dk'] >= 960: label = 'Akşam'
            
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
unavailable_days_input = st.sidebar.text_area("Günlük Muafiyet", placeholder="Örn: 1:Pazartesi (1. Hafta)")
unavailable_times_input = st.sidebar.text_area("Saatlik Muafiyet", placeholder="Örn: 1:16:00-21:00")

st.sidebar.divider()
st.sidebar.header("🎯 İş Yükü Dağılım Stratejileri")
w_total = st.sidebar.number_input("Toplam Süre Dengesi", 0, 100, 20)
w_big = st.sidebar.number_input("Büyük Salon Dağılımı", 0, 100, 20)
w_morn = st.sidebar.number_input("Sabah Seansı Dengesi", 0, 100, 20)
w_eve = st.sidebar.number_input("Akşam Seansı Dengesi", 0, 100, 20)
w_sa_total = st.sidebar.number_input("Kritik Seans Dağılımı", 0, 100, 20)

if uploaded_file:
    tasks, rooms, days_list = parse_excel(uploaded_file)
    big_rooms = st.sidebar.multiselect("Büyük Salonlar", rooms, default=[r for r in rooms if r in ['301', '303', '304']])
    
    if st.sidebar.button("Optimizasyon Sürecini Başlat"):
        if (w_total + w_big + w_morn + w_eve + w_sa_total) != 100:
            st.sidebar.error("⚠️ Strateji ağırlıkları toplamı 100 olmalıdır.")
        else:
            with st.spinner('Operasyonel planlama optimize ediliyor...'):
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

                # İŞ YÜKÜ FARKI SINIRI: Max - Min <= 2
                max_e, min_e = model.NewIntVar(0, 100, 'max_e'), model.NewIntVar(0, 100, 'min_e')
                model.AddMaxEquality(max_e, [total_exams[i] for i in invs])
                model.AddMinEquality(min_e, [total_exams[i] for i in invs])
                model.Add(max_e - min_e <= 2)

                def get_diff(v_dict, subset, name):
                    if not subset: return 0
                    vals = [v_dict[idx] for idx in subset]
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
                    st.success("✅ Operasyonel görev planlaması başarıyla tamamlanmıştır.")
                    res = []
                    for t_idx, t in enumerate(tasks):
                        for i in invs:
                            if solver.Value(x[i, t_idx]):
                                row = t.copy(); row['Görevli Personel'] = i; res.append(row)
                    
                    df_res = pd.DataFrame(res)
                    tab1, tab2, tab3 = st.tabs(["📋 Görev Çizelgesi", "📊 Hakkaniyetli Görev Dağılım Analizi", "📖 Uygulama Metodolojisi"])
                    with tab1:
                        final_df = df_res[['Gün', 'Ders Adı', 'Sınav Saati', 'Sınav Salonu', 'Görevli Personel']]
                        st.dataframe(final_df, use_container_width=True)
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                            final_df.to_excel(writer, index=False, sheet_name='Plan')
                        st.download_button("📥 Çizelgeyi İndir", buffer.getvalue(), "gozetmen_plani.xlsx")
                    
                    with tab2:
                        stats = []
                        for i in invs:
                            tag = " (Muaf)" if i in restricted_staff else ""
                            stats.append({
                                "Personel": f"{i}{tag}", "Toplam Süre (Dk)": solver.Value(total_mins[i]), 
                                "Büyük Salon Süresi": solver.Value(big_mins[i]), "Toplam Görev Sayısı": solver.Value(total_exams[i]),
                                "Sabah Seansı Sayısı": solver.Value(morn_cnt[i]), "Akşam Seansı Sayısı": solver.Value(eve_cnt[i]), 
                                "Kritik Seans Toplamı": solver.Value(critical_sum[i])
                            })
                        st.table(pd.DataFrame(stats))
                    
                    with tab3:
                        st.subheader("Sistem Çalışma Prensipleri")
                        st.write("Bu yazılım, sınav gözetmenliği planlama sürecini operasyonel verimlilik ve hakkaniyetli dağılım ilkeleri çerçevesinde yürütür.")

                        st.markdown("### Süreç Analizi ve Dönem Tespiti")
                        st.write("""
                        Sistem, yüklenen takvimi detaylı bir şekilde tarayarak hafta geçişlerini otomatik olarak belirler. Günlerin takvim akışına göre (örneğin Cuma'dan sonra Pazartesi'ye dönüş) programın kaç haftadan oluştuğunu anlar ve çizelgeyi buna göre isimlendirir. 
                        
                        Her takvim gününün başlayan ilk sınavı 'Sabah Seansı' olarak damgalanır. 'Akşam Mesaisi' tanımı ise programın süresine göre dinamik olarak değişir: 
                        Tek haftalık programlarda saat 16:00 ve sonrası esas alınırken; çok haftalık programlarda o günün gerçekleşen en son sınavı akşam seansı olarak kabul edilir.
                        """)

                        st.markdown("### Operasyonel Standartlar")
                        st.write("""
                        Görev dağılımı yapılırken aşağıdaki kurallar sistem tarafından her zaman uygulanır:
                        - Bir personel aynı zaman aralığında yalnızca tek bir sınav salonunda görev alabilir; zaman çakışmaları tamamen engellenmiştir.
                        - Günlük iş yükünü dengede tutmak adına, bir personelin bir takvim günü içerisindeki maksimum görev sayısı dört ile sınırlandırılmıştır.
                        - Hakkaniyetli dağılımı garanti altına almak amacıyla, programın tamamı boyunca en çok görev alan personel ile en az görev alan personel arasındaki fark ikiden fazla olamaz.
                        - Tanımlanan tüm personel muafiyetleri sisteme öncelikli kural olarak işlenir ve kısıtlı zaman dilimlerinde atama yapılmaz.
                        """)

                        st.markdown("### İş Yükü Optimizasyonu")
                        st.write("""
                        Yazılım, görev sayılarını eşitlemenin ötesinde personelin harcadığı toplam süreyi ve büyük kapasiteli salonlardaki mesai yükünü de dengeler. Tüm bu veriler bütünleşik bir yapıda, programın tamamı üzerinden optimize edilir.
                        
                        Saatlik bazda kısıtlaması bulunan personel, sabah veya akşam seansı gibi özel dağılım hesaplamalarının dışında tutulur. Bu yaklaşım, kısıtlı personelin mecburen düşük olan belirli seans sayılarının genel ortalamayı yanıltmasını önler ve diğer personellerin kendi aralarında en verimli şekilde dengelenmesini sağlar.
                        """)

                        st.markdown("### Süreç Verimliliği")
                        st.write("""
                        Personelin kampüs içerisinde geçirdiği zamanın verimli kullanılması temel hedeflerden biridir. Bu doğrultuda sistem, kümelenme yöntemini kullanarak bir personeli günün son görevlerine atarken mümkünse birden fazla akşam görevini aynı kişiye yönlendirir. Böylece personelin bulunduğu sürede görevlerini tamamlaması sağlanırken, diğer personellerin gereksiz yere geç saatlere kadar beklemesi önlenir.
                        """)
                else:
                    st.error("❌ Belirlenen kriterler dahilinde uygun bir planlama üretilemedi. Personel sayısı ile görev yükü arasındaki dengeyi kontrol edebilir veya muafiyetleri esnetebilirsiniz.")
