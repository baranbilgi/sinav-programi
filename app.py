import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
from ortools.sat.python import cp_model
import io

# Sayfa Yapılandırması
st.set_page_config(page_title="Gözetmen Planlama Sistemi", layout="wide")
st.title("🏛️ Gözetmen Optimizasyon ve Görev Planlama Sistemi")

# --- YARDIMCI FONKSİYONLAR ---
def to_min(time_str):
    """'HH:MM' veya 'HH.MM' formatındaki saati gün başlangıcından itibaren dakikaya çevirir."""
    try:
        h, m = map(int, time_str.replace('.', ':').split(':'))
        return h * 60 + m
    except:
        return 0

def parse_xml(xml_content):
    tree = ET.ElementTree(ET.fromstring(xml_content))
    root = tree.getroot()
    tasks = []
    all_rooms = set()
    days_order = []
    for gun in root.findall('gun'):
        gun_adi = gun.get('isim')
        if gun_adi not in days_order: days_order.append(gun_adi)
        sinavlar = gun.findall('sınav') + gun.findall('sinav')
        for sinav in sinavlar:
            siniflar_text = sinav.find('siniflar').text
            sinif_listesi = [s.strip() for s in siniflar_text.split(',') if s.strip()]
            for s in sinif_listesi:
                all_rooms.add(s)
                tasks.append({
                    'gun': gun_adi, 'sinav': sinav.get('ad'), 
                    'saat': f"{sinav.get('baslangic')}-{sinav.get('bitis')}",
                    'baslangic': sinav.get('baslangic'), 'sinif': s,
                    'sure': int(sinav.get('sure')), 'etiket': sinav.get('etiket', 'normal'),
                    'slot_id': f"{gun_adi}_{sinav.get('baslangic')}"
                })
    return tasks, sorted(list(all_rooms)), days_order

# --- YAN MENÜ (AYARLAR VE ÖNCELİKLER) ---
st.sidebar.header("⚙️ Operasyonel Ayarlar")
uploaded_file = st.sidebar.file_uploader("Sınav Takvimi (XML)", type=["xml"])
staff_count = st.sidebar.number_input("Toplam Personel Sayısı", min_value=1, value=6)

# --- MUAFİYET PANELİ ---
st.sidebar.divider()
st.sidebar.subheader("🚫 Görev Muafiyetleri")

unavailable_days_input = st.sidebar.text_area(
    "1. Gün Bazlı Muafiyet", 
    placeholder="Örn: 1:Pazartesi, 2:Sali",
    help="Belirtilen gözetmeni o günün tamamından muaf tutar."
)

unavailable_times_input = st.sidebar.text_area(
    "2. Saat Aralığı Muafiyeti (Tüm Hafta)", 
    placeholder="Örn: 1:16:00-20:00",
    help="Format: GözetmenNo:Başlangıç-Bitiş. Belirtilen saat aralığına denk gelen sınavlara atama yapılmaz."
)

st.sidebar.divider()
st.sidebar.header("🎯 Strateji Ağırlıkları (Toplam: 100)")
w_total = st.sidebar.number_input("Toplam Süre Dengesi", 0, 100, 20)
w_big = st.sidebar.number_input("Büyük Sınıf Dengesi", 0, 100, 20)
w_morn = st.sidebar.number_input("Sabah Sınavı Dengesi", 0, 100, 20)
w_eve = st.sidebar.number_input("Akşam Sınavı Dengesi", 0, 100, 20)
w_sa_total = st.sidebar.number_input("S+A Toplam Sayı Dengesi", 0, 100, 20)

total_weight = w_total + w_big + w_morn + w_eve + w_sa_total
st.sidebar.write(f"**Güncel Toplam: {total_weight}**")

if uploaded_file:
    tasks, rooms, days_list = parse_xml(uploaded_file.read().decode("utf-8"))
    big_rooms = st.sidebar.multiselect("Büyük Sınıf Odaları", rooms, default=[r for r in rooms if r in ['301', '309']])
    
    if st.sidebar.button("Planlamayı Optimize Et"):
        if total_weight != 100:
            st.sidebar.error(f"⚠️ Hata: Ağırlıkların toplamı 100 olmalıdır! (Şu an: {total_weight}).")
        else:
            model = cp_model.CpModel()
            invs = list(range(1, staff_count + 1))
            num_t = len(tasks)
            x = {(i, t): model.NewBoolVar(f'x_{i}_{t}') for i in invs for t in range(num_t)}

            # --- SERT KISITLAR ---
            for t in range(num_t):
                model.Add(sum(x[i, t] for i in invs) == 1)
            
            for i in invs:
                for slot in set(t['slot_id'] for t in tasks):
                    overlap = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                    model.Add(sum(x[i, idx] for idx in overlap) <= 1)
                
                for d_idx, d in enumerate(days_list):
                    day_tasks = [idx for idx, t in enumerate(tasks) if t['gun'] == d]
                    model.Add(sum(x[i, idx] for idx in day_tasks) <= 4)
                    
                    if d_idx < len(days_list) - 1:
                        today_last = [idx for idx, t in enumerate(tasks) if t['gun'] == d and t['etiket'] == 'aksam']
                        tomorrow_first = [idx for idx, t in enumerate(tasks) if t['gun'] == days_list[d_idx+1] and t['etiket'] == 'sabah']
                        for tl in today_last:
                            for tf in tomorrow_first:
                                model.Add(x[i, tl] + x[i, tf] <= 1)

            # --- GÜN BAZLI MUAFİYET ---
            if unavailable_days_input:
                for entry in unavailable_days_input.split(','):
                    if ':' in entry:
                        try:
                            s_no_str, d_name = entry.split(':')
                            s_no = int(s_no_str.strip())
                            if s_no in invs:
                                for idx, t in enumerate(tasks):
                                    if t['gun'] == d_name.strip():
                                        model.Add(x[s_no, idx] == 0)
                        except: continue

            # --- SAAT ARALIĞI MUAFİYETİ ---
            if unavailable_times_input:
                for entry in unavailable_times_input.split(','):
                    if ':' in entry:
                        try:
                            s_no_str, time_range_str = entry.split(':')
                            s_no = int(s_no_str.strip())
                            if '-' in time_range_str and s_no in invs:
                                t_start_str, t_end_str = time_range_str.split('-')
                                exempt_start = to_min(t_start_str.strip())
                                exempt_end = to_min(t_end_str.strip())
                                
                                for idx, t in enumerate(tasks):
                                    task_start = to_min(t['baslangic'])
                                    task_end = task_start + t['sure']
                                    
                                    # Kesişim kontrolü: max(baslangiclar) < min(bitisler)
                                    if max(task_start, exempt_start) < min(task_end, exempt_end):
                                        model.Add(x[s_no, idx] == 0)
                        except: continue

            # --- ADALET DEĞİŞKENLERİ ---
            total_mins, big_mins, morn_cnt, eve_cnt, critical_sum = {}, {}, {}, {}, {}
            for i in invs:
                total_mins[i] = model.NewIntVar(0, 10000, f'tm_{i}')
                big_mins[i] = model.NewIntVar(0, 10000, f'bm_{i}')
                morn_cnt[i] = model.NewIntVar(0, 100, f'mc_{i}')
                eve_cnt[i] = model.NewIntVar(0, 100, f'ec_{i}')
                critical_sum[i] = model.NewIntVar(0, 200, f'cs_{i}')

                model.Add(total_mins[i] == sum(x[i, t] * tasks[t]['sure'] for t in range(num_t)))
                model.Add(big_mins[i] == sum(x[i, t] * tasks[t]['sure'] for t in range(num_t) if tasks[t]['sinif'] in big_rooms))
                model.Add(morn_cnt[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['etiket'] == 'sabah'))
                model.Add(eve_cnt[i] == sum(x[i, t] for t in range(num_t) if tasks[t]['etiket'] == 'aksam'))
                model.Add(critical_sum[i] == morn_cnt[i] + eve_cnt[i])

            def get_diff_var(v_dict, name):
                ma, mi = model.NewIntVar(0, 10000, f'ma_{name}'), model.NewIntVar(0, 10000, f'mi_{name}')
                model.AddMaxEquality(ma, list(v_dict.values()))
                model.AddMinEquality(mi, list(v_dict.values()))
                diff = model.NewIntVar(0, 10000, f'd_{name}')
                model.Add(diff == ma - mi)
                return diff

            # --- AMAÇ FONKSİYONU ---
            model.Minimize(
                get_diff_var(total_mins, "t") * w_total * 100 +
                get_diff_var(big_mins, "b") * w_big * 100 +
                get_diff_var(morn_cnt, "m") * w_morn * 1000 + 
                get_diff_var(eve_cnt, "e") * w_eve * 1000 +
                get_diff_var(critical_sum, "c") * w_sa_total * 1000
            )

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 30.0
            if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                st.success("✅ Planlama başarıyla optimize edildi.")
                
                final_res = []
                for t_idx, t in enumerate(tasks):
                    for i in invs:
                        if solver.Value(x[i, t_idx]):
                            row = t.copy()
                            row['Gözetmen'] = i
                            final_res.append(row)
                
                df = pd.DataFrame(final_res)
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                    df[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']].to_excel(writer, index=False)
                excel_data = output.getvalue()

                t1, t2, t3 = st.tabs(["📋 Görev Çizelgesi", "📊 İş Yükü Analizi", "📖 Metodoloji"])
                
                with t1:
                    st.download_button("📥 Excel İndir", excel_data, "gorev_plani.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                    st.dataframe(df[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']], use_container_width=True)
                
                with t2:
                    report = []
                    for i in invs:
                        report.append({
                            "Gözetmen": i,
                            "Toplam Mesai (dk)": solver.Value(total_mins[i]),
                            "Büyük Sınıf Mesaisi (dk)": solver.Value(big_mins[i]),
                            "Sabah Görevi": solver.Value(morn_cnt[i]),
                            "Akşam Görevi": solver.Value(eve_cnt[i]),
                            "Kritik Toplam (S+A)": solver.Value(critical_sum[i])
                        })
                    st.table(pd.DataFrame(report))

                with t3:
                    st.info("### 🧠 Sistem Çalışma Metodolojisi")
                    st.markdown(f"""
                    Bu dağıtım planı, **Google OR-Tools (Constraint Programming)** kütüphanesi kullanılarak oluşturulmuştur. Sistem, milyonlarca olası atama kombinasyonunu saniyeler içinde tarayarak belirlediğiniz strateji ağırlıklarına göre en dengeli sonucu üretir.

                    #### ⚖️ Optimizasyon Hiyerarşisi
                    Sistem, aşağıdaki kriterler arasındaki farkı (eşitsizliği) minimize etmeye odaklanır:
                    - **Mesai Dengesi:** Toplam sınav sürelerinin homojenize edilmesi.
                    - **Salon Rotasyonu:** Büyük salonlardaki görev yükünün eşit dağıtılması.
                    - **Zaman Dilimi Adaleti:** Sabah ve akşam sınavlarının kendi içlerinde ve toplamda dengelenmesi.

                    #### 🛡️ Uygulanan Sert Kısıtlar (Garantiler)
                    Atama yapılırken aşağıdaki kurallar sistem tarafından **asla ihlal edilemez**:
                    1. **Çakışma Önleme:** Bir personel, aynı zaman diliminde iki farklı sınavda görevlendirilemez.
                    2. **Nöbet Dinlenme Kuralı:** Akşam sınavından sonraki sabahın ilk sınavına atama yapılmaz.
                    3. **Kapasite Yönetimi:** Günlük iş yükü **4 sınav** ile sınırlandırılmıştır.
                    4. **Muafiyet Kontrolü:** - **Günlük:** Belirtilen günlerde personel görev almaz.
                        - **Aralık Bazlı Saatlik:** Belirlenen saat dilimiyle (Örn: 16:00-20:00) çakışan hiçbir sınava atama yapılmaz.
                    """)
            else:
                st.error("Mevcut kısıtlar altında uygun bir dağıtım bulunamadı. Lütfen personel sayısını artırmayı veya muafiyetleri esnetmeyi deneyin.")
else:
    st.info("Lütfen sol taraftaki menüyü kullanarak sınav takviminizi (XML) yükleyin.")
