import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
from ortools.sat.python import cp_model
import io
import re

# Sayfa Yapılandırması
st.set_page_config(page_title="Gözetmen Planlama Sistemi", layout="wide")
st.title("🏛️ Gözetmen Optimizasyon ve Görev Planlama Sistemi")

# --- YARDIMCI FONKSİYONLAR ---
def to_min(time_str):
    if not time_str: return None
    try:
        clean_time = re.sub(r'[^0-9:]', ':', time_str.replace('.', ':')).strip()
        h, m = map(int, clean_time.split(':'))
        return h * 60 + m
    except:
        return None

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
            
            etiket = sinav.get('etiket', 'normal')
            bas_saat = to_min(sinav.get('baslangic'))
            if etiket == 'normal' and bas_saat is not None:
                if bas_saat <= 600: etiket = 'sabah'
                elif bas_saat >= 1020: etiket = 'aksam'

            for s in sinif_listesi:
                all_rooms.add(s)
                tasks.append({
                    'gun': gun_adi, 'sinav': sinav.get('ad'), 
                    'saat': f"{sinav.get('baslangic')}-{sinav.get('bitis')}",
                    'baslangic': sinav.get('baslangic').strip(), 
                    'sinif': s, 'sure': int(sinav.get('sure')), 
                    'etiket': etiket, 'slot_id': f"{gun_adi}_{sinav.get('baslangic')}"
                })
    return tasks, sorted(list(all_rooms)), days_order

# --- YAN MENÜ ---
st.sidebar.header("⚙️ Operasyonel Ayarlar")
uploaded_file = st.sidebar.file_uploader("Sınav Takvimi (XML)", type=["xml"])
staff_count = st.sidebar.number_input("Toplam Personel Sayısı", min_value=1, value=6)

st.sidebar.divider()
st.sidebar.subheader("🚫 Görev Muafiyetleri")
unavailable_days_input = st.sidebar.text_area("1. Görev Muafiyeti Gün", placeholder="Örn: 1:Pazartesi")
unavailable_times_input = st.sidebar.text_area("2. Görev Muafiyeti Saat (Aralık)", placeholder="Örn: 1:08:00-12:00")

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
            st.sidebar.error("⚠️ Ağırlık toplamı 100 olmalıdır!")
        else:
            model = cp_model.CpModel()
            invs = list(range(1, staff_count + 1))
            num_t = len(tasks)
            x = {(i, t): model.NewBoolVar(f'x_{i}_{t}') for i in invs for t in range(num_t)}

            evening_clusters = []

            for i in invs:
                # Sert Kısıtlar
                for slot in set(t['slot_id'] for t in tasks):
                    overlap = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                    model.Add(sum(x[i, idx] for idx in overlap) <= 1)
                
                for d_idx, d in enumerate(days_list):
                    day_tasks_idx = [idx for idx, t in enumerate(tasks) if t['gun'] == d]
                    model.Add(sum(x[i, idx] for idx in day_tasks_idx) <= 4)
                    
                    # Akşam Teşviki (Aynı gün akşam mesaisi birleştirme)
                    eve_tasks_in_day = [idx for idx in day_tasks_idx if tasks[idx]['etiket'] == 'aksam']
                    if len(eve_tasks_in_day) > 1:
                        has_multiple_eve = model.NewBoolVar(f'multi_eve_{i}_{d}')
                        model.Add(sum(x[i, idx] for idx in eve_tasks_in_day) >= 2).OnlyEnforceIf(has_multiple_eve)
                        evening_clusters.append(has_multiple_eve)

            # Atama zorunluluğu
            for t in range(num_t):
                model.Add(sum(x[i, t] for i in invs) == 1)

            # Muafiyet İşlemleri
            if unavailable_days_input:
                for entry in unavailable_days_input.split(','):
                    if ':' in entry:
                        try:
                            s_no, d_name = entry.split(':')
                            s_no = int(s_no.strip())
                            if s_no in invs:
                                for idx, t in enumerate(tasks):
                                    if t['gun'].strip().lower() == d_name.strip().lower(): model.Add(x[s_no, idx] == 0)
                        except: continue

            if unavailable_times_input:
                for entry in unavailable_times_input.split(','):
                    if ':' in entry:
                        try:
                            parts = entry.split(':', 1)
                            s_no, t_range = int(parts[0]), parts[1].strip()
                            st_str, en_str = t_range.split('-')
                            ex_s, ex_e = to_min(st_str), to_min(en_str)
                            for idx, t in enumerate(tasks):
                                ts, te = to_min(t['baslangic']), to_min(t['baslangic']) + t['sure']
                                if max(ts, ex_s) < min(te, ex_e): model.Add(x[s_no, idx] == 0)
                        except: continue

            # Adalet Değişkenleri
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

            def get_diff(v_dict, name):
                ma, mi = model.NewIntVar(0, 10000, f'ma_{name}'), model.NewIntVar(0, 10000, f'mi_{name}')
                model.AddMaxEquality(ma, list(v_dict.values())); model.AddMinEquality(mi, list(v_dict.values()))
                d = model.NewIntVar(0, 10000, f'd_{name}'); model.Add(d == ma - mi)
                return d

            # AMAÇ FONKSİYONU
            model.Minimize(
                get_diff(total_mins, "t") * w_total * 100 +
                get_diff(big_mins, "b") * w_big * 100 +
                get_diff(morn_cnt, "m") * w_morn * 1000 + 
                get_diff(eve_cnt, "e") * w_eve * 1000 +
                get_diff(critical_sum, "c") * w_sa_total * 1000 -
                sum(evening_clusters) * 5000 
            )

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 30.0
            
            if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                st.success("✅ Optimizasyon işlemi başarıyla tamamlandı ve planlama oluşturuldu.")
                
                res = []
                for t_idx, t in enumerate(tasks):
                    for i in invs:
                        if solver.Value(x[i, t_idx]):
                            row = t.copy(); row['Gözetmen'] = i; res.append(row)
                df_res = pd.DataFrame(res)
                
                t1, t2, t3 = st.tabs(["📋 Çizelge", "📊 Analiz", "🧠 Metodoloji"])
                
                with t1:
                    st.dataframe(df_res[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']], use_container_width=True)
                
                with t2:
                    stats = []
                    for i in invs:
                        stats.append({
                            "Gözetmen": i, 
                            "Toplam Mesai (dk)": solver.Value(total_mins[i]), 
                            "Büyük Sınıf (dk)": solver.Value(big_mins[i]), 
                            "Sabah": solver.Value(morn_cnt[i]), 
                            "Akşam": solver.Value(eve_cnt[i]), 
                            "Kritik Toplam": solver.Value(critical_sum[i])
                        })
                    st.table(pd.DataFrame(stats))
                
                with t3:
                    st.markdown("### 🧠 Gelişmiş Optimizasyon Metodolojisi")
                    st.write("""
                    Bu sistem, karmaşık zamanlama problemlerini çözmek için geliştirilen **Google OR-Tools** kütüphanesinin 
                    **CP-SAT (Constraint Programming - Satisfiability)** çözücüsünü kullanmaktadır. 
                    """)
                    
                    st.info("#### ⚙️ Kullanılan Algoritmik Mantık")
                    st.markdown("""
                    **1. Kısıt Programlama (Constraint Programming):** Geleneksel algoritmaların aksine, CP-SAT 'nelerin olamayacağına' odaklanır. 
                    - *Sert Kısıtlar:* Bir gözetmenin aynı anda iki farklı sınavda olması veya günlük görev limitini aşması matematiksel olarak engellenir.
                    - *Yumuşak Kısıtlar:* Ağırlıklı puanlama ile ideal senaryoya yaklaşılır.

                    **2. SAT-Based Search & Lazy Clause Generation:** Model, problemleri Boolean (0-1) mantığına indirger. Bu yöntem, devasa olasılık uzaylarını (trilyonlarca kombinasyon) saniyeler içinde tarayarak çakışmasız en iyi sonucu bulur.

                    **3. Min-Max Normalizasyonu (Adalet Mekanizması):** Sistem, en yoğun çalışan gözetmen ile en az çalışan arasındaki farkı minimize etmeye odaklanır.
                    """)
                    
                    st.latex(r"Minimize: \sum_{i \in Criteria} (Weight_i \times (Max_i - Min_i)) - Reward_{cluster}")
                    
                    st.markdown("""
                    **4. Kümelenme Stratejisi (Evening Clustering):** Personel verimliliğini artırmak adına, eğer bir gözetmen akşam sınavına atanmışsa, sistem o kişiyi kampüsten erken göndermek veya gelişi-gidişi optimize etmek için uygun diğer akşam sınavlarına öncelikli olarak yerleştirir.
                    """)

            else: 
                st.error("❌ Belirtilen kısıtlar altında uygun bir çözüm bulunamadı! Lütfen personel sayısını artırmayı veya muafiyetleri azaltmayı deneyin.")

