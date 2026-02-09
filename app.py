import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
from ortools.sat.python import cp_model
import io

# Sayfa Yapılandırması
st.set_page_config(page_title="Gözetmen Planlama Sistemi", layout="wide")
st.title("🏛️ Gözetmen Optimizasyon ve Görev Planlama Sistemi")

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

st.sidebar.divider()
st.sidebar.header("🎯 Öncelik Ağırlıkları (Toplam: 100)")
w_total = st.sidebar.number_input("Toplam Süre Dengesi", 0, 100, 70)
w_big = st.sidebar.number_input("Büyük Sınıf Dengesi", 0, 100, 20)
w_morn_eve = st.sidebar.number_input("Sabah/Akşam Kendi İçinde Eşitlik", 0, 100, 7)
w_sa_total = st.sidebar.number_input("S+A Toplam Sayı Eşitliği", 0, 100, 3)

total_weight = w_total + w_big + w_morn_eve + w_sa_total
st.sidebar.write(f"**Güncel Toplam: {total_weight}**")

if uploaded_file:
    tasks, rooms, days_list = parse_xml(uploaded_file.read().decode("utf-8"))
    big_rooms = st.sidebar.multiselect("Büyük Sınıf Odaları", rooms, default=[r for r in rooms if r in ['301', '309']])
    
    if st.sidebar.button("Planlamayı Optimize Et"):
        if total_weight != 100:
            st.sidebar.error("⚠️ Hata: Ağırlıkların toplamı tam olarak 100 olmalıdır! Lütfen değerleri düzeltin.")
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
                    model.Add(sum(x[i, idx] for idx in day_tasks) <= 3)
                    if d_idx < len(days_list) - 1:
                        today_last = [idx for idx, t in enumerate(tasks) if t['gun'] == d and t['etiket'] == 'aksam']
                        tomorrow_first = [idx for idx, t in enumerate(tasks) if t['gun'] == days_list[d_idx+1] and t['etiket'] == 'sabah']
                        for tl in today_last:
                            for tf in tomorrow_first:
                                model.Add(x[i, tl] + x[i, tf] <= 1)

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

            # --- AMAÇ FONKSİYONU (Kullanıcı Ağırlıklı) ---
            model.Minimize(
                get_diff_var(total_mins, "t") * w_total * 100 + # Ölçeklendirme için 100 ile çarpıldı
                get_diff_var(big_mins, "b") * w_big * 100 +
                get_diff_var(morn_cnt, "m") * w_morn_eve * 1000 + # Sayısal veriler (adet) daha yüksek çarpan ister
                get_diff_var(eve_cnt, "e") * w_morn_eve * 1000 +
                get_diff_var(critical_sum, "c") * w_sa_total * 1000
            )

            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = 30.0
            if solver.Solve(model) in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                st.success(f"✅ Belirlenen önceliklerle ({w_total}/{w_big}/{w_morn_eve}/{w_sa_total}) plan oluşturuldu.")
                
                final_res = []
                for t_idx, t in enumerate(tasks):
                    for i in invs:
                        if solver.Value(x[i, t_idx]):
                            row = t.copy()
                            row['Gözetmen'] = f"Gözetmen {i}"
                            final_res.append(row)
                
                df = pd.DataFrame(final_res)
                t1, t2 = st.tabs(["📋 Çizelge", "📊 Analiz"])
                with t1: st.dataframe(df[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen']], use_container_width=True)
                with t2:
                    report = []
                    for i in invs:
                        report.append({
                            "Gözetmen": f"Gözetmen {i}",
                            "Süre (dk)": solver.Value(total_mins[i]),
                            "Büyük Sınıf (dk)": solver.Value(big_mins[i]),
                            "Sabah": solver.Value(morn_cnt[i]),
                            "Akşam": solver.Value(eve_cnt[i]),
                            "S+A Toplam": solver.Value(critical_sum[i])
                        })
                    st.table(pd.DataFrame(report))
            else:
                st.error("Bu kısıtlar ve ağırlıklarla çözüm bulunamadı.")
