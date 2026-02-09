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
        # Saat formatını temizle (12:15, 12.15 vb. durumlar için)
        clean_time = re.sub(r'[^0-9:]', ':', str(time_str).replace('.', ':')).strip()
        if ':' not in clean_time: return None
        h, m = map(int, clean_time.split(':')[:2])
        return h * 60 + m
    except:
        return None

def parse_excel(file):
    df = pd.read_excel(file)
    # Sütun isimlerini normalize et (Büyük/Küçük harf duyarlılığını azaltmak için)
    df.columns = [c.strip().upper() for c in df.columns]
    
    tasks = []
    all_rooms = set()
    days_order = []
    
    # Beklenen sütunlar: GÜN, DERSLER, SAAT, SINAV YERİ
    for _, row in df.iterrows():
        if pd.isna(row.get('GÜN')) or pd.isna(row.get('SAAT')):
            continue
            
        gun_adi = str(row['GÜN']).strip()
        if gun_adi not in days_order: 
            days_order.append(gun_adi)
            
        ders_adi = str(row.get('DERSLER', 'Bilinmeyen Ders'))
        saat_araligi = str(row['SAAT'])
        sinav_yerleri = str(row.get('SINAV YERİ', ''))
        
        # Saat parçalama (Örn: 12:15-13:00)
        try:
            bas_str, bit_str = saat_araligi.split('-')
            bas_dakika = to_min(bas_str)
            bit_dakika = to_min(bit_str)
            sure = bit_dakika - bas_dakika
        except:
            continue

        # Akşam Etiketleme Mantığı: 16:00 ve sonrası (16*60 = 960 dk)
        etiket = 'normal'
        if bas_dakika is not None:
            if bas_dakika >= 960: # 16:00 ve sonrası
                etiket = 'aksam'
            elif bas_dakika <= 600: # 10:00 ve öncesi (sabah tanımı)
                etiket = 'sabah'

        # Sınav yerlerini ayır (301-303 -> [301, 303])
        sinif_listesi = [s.strip() for s in sinav_yerleri.replace(',', '-').split('-') if s.strip()]
        
        for s in sinif_listesi:
            all_rooms.add(s)
            tasks.append({
                'gun': gun_adi, 
                'sinav': ders_adi, 
                'saat': saat_araligi,
                'baslangic': bas_str.strip(), 
                'bas_dk': bas_dakika,
                'sinif': s, 
                'sure': sure, 
                'etiket': etiket, 
                'slot_id': f"{gun_adi}_{bas_str.strip()}"
            })
            
    return tasks, sorted(list(all_rooms)), days_order

# --- YAN MENÜ ---
st.sidebar.header("⚙️ Operasyonel Ayarlar")
uploaded_file = st.sidebar.file_uploader("Sınav Takvimi (Excel)", type=["xlsx", "xls"])
staff_count = st.sidebar.number_input("Toplam Personel Sayısı", min_value=1, value=15)

st.sidebar.divider()
st.sidebar.subheader("🚫 Görev Muafiyetleri")
st.sidebar.caption("Format: PersonelNo:Gün veya PersonelNo:SaatAralığı")
unavailable_days_input = st.sidebar.text_area("1. Günlük Muafiyet", placeholder="Örn: 1:Pazartesi")
unavailable_times_input = st.sidebar.text_area("2. Saatlik Muafiyet", placeholder="Örn: 1:08:00-12:00")

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
    tasks, rooms, days_list = parse_excel(uploaded_file)
    big_rooms = st.sidebar.multiselect("Büyük Sınıf Odaları", rooms, default=[r for r in rooms if r in ['301', '303', '304']])
    
    if st.sidebar.button("Planlamayı Optimize Et"):
        if total_weight != 100:
            st.sidebar.error("⚠️ Strateji ağırlıklarının toplamı tam olarak 100 olmalıdır!")
        else:
            with st.spinner('Matematiksel model çözülüyor, lütfen bekleyin...'):
                model = cp_model.CpModel()
                invs = list(range(1, staff_count + 1))
                num_t = len(tasks)
                
                # Karar Değişkeni: x[gözetmen, görev]
                x = {(i, t): model.NewBoolVar(f'x_{i}_{t}') for i in invs for t in range(num_t)}

                evening_clusters = []

                for i in invs:
                    # 1. Çakışma Kısıtı: Bir gözetmen aynı anda iki yerde olamaz
                    for slot in set(t['slot_id'] for t in tasks):
                        overlap = [idx for idx, t in enumerate(tasks) if t['slot_id'] == slot]
                        model.Add(sum(x[i, idx] for idx in overlap) <= 1)
                    
                    # 2. Günlük Yük Kısıtı: Bir günde max 4 sınav (isteğe bağlı değiştirilebilir)
                    for d in days_list:
                        day_tasks_idx = [idx for idx, t in enumerate(tasks) if t['gun'] == d]
                        model.Add(sum(x[i, idx] for idx in day_tasks_idx) <= 4)
                        
                        # 3. Akşam Teşviki (16:00 sonrası görevlerin aynı kişide toplanması)
                        eve_tasks_in_day = [idx for idx in day_tasks_idx if tasks[idx]['etiket'] == 'aksam']
                        if len(eve_tasks_in_day) > 1:
                            has_multiple_eve = model.NewBoolVar(f'multi_eve_{i}_{d}')
                            # Eğer bu gözetmen o gün 2 veya daha fazla akşam sınavına girerse teşvik puanı verilir
                            model.Add(sum(x[i, idx] for idx in eve_tasks_in_day) >= 2).OnlyEnforceIf(has_multiple_eve)
                            evening_clusters.append(has_multiple_eve)

                # 4. Atama Kısıtı: Her sınava mutlaka 1 gözetmen atanmalı
                for t in range(num_t):
                    model.Add(sum(x[i, t] for i in invs) == 1)

                # 5. Muafiyetleri Uygula
                if unavailable_days_input:
                    for entry in unavailable_days_input.split(','):
                        try:
                            s_no, d_name = entry.split(':')
                            s_no = int(s_no.strip())
                            if s_no in invs:
                                for idx, t in enumerate(tasks):
                                    if t['gun'].strip().lower() == d_name.strip().lower(): 
                                        model.Add(x[s_no, idx] == 0)
                        except: continue

                if unavailable_times_input:
                    for entry in unavailable_times_input.split(','):
                        try:
                            parts = entry.split(':', 1)
                            s_no, t_range = int(parts[0]), parts[1].strip()
                            st_str, en_str = t_range.split('-')
                            ex_s, ex_e = to_min(st_str), to_min(en_str)
                            for idx, t in enumerate(tasks):
                                ts, te = t['bas_dk'], t['bas_dk'] + t['sure']
                                if max(ts, ex_s) < min(te, ex_e): 
                                    model.Add(x[s_no, idx] == 0)
                        except: continue

                # Adalet ve Dengeleme Değişkenleri
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
                    model.AddMaxEquality(ma, list(v_dict.values()))
                    model.AddMinEquality(mi, list(v_dict.values()))
                    d = model.NewIntVar(0, 10000, f'd_{name}')
                    model.Add(d == ma - mi)
                    return d

                # AMAÇ FONKSİYONU: Farkları minimize et, akşam kümelenmesini maksimize et
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
                
                status = solver.Solve(model)
                
                if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
                    st.success("✅ Optimizasyon işlemi başarıyla tamamlandı ve planlama oluşturuldu.")
                    
                    res = []
                    for t_idx, t in enumerate(tasks):
                        for i in invs:
                            if solver.Value(x[i, t_idx]):
                                row = t.copy()
                                row['Gözetmen'] = f"Personel {i}"
                                res.append(row)
                    
                    df_res = pd.DataFrame(res)
                    t1, t2, t3 = st.tabs(["📋 Görev Çizelgesi", "📊 Adalet Analizi", "🧠 Metodoloji"])
                    
                    with t1:
                        output_df = df_res[['gun', 'sinav', 'saat', 'sinif', 'Gözetmen', 'etiket']]
                        st.dataframe(output_df, use_container_width=True)
                        
                        # Excel İndirme
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                            output_df.to_excel(writer, index=False, sheet_name='Plan')
                        st.download_button("📥 Sonucu Excel Olarak İndir", buffer.getvalue(), "gozetmen_plani.xlsx")
                    
                    with t2:
                        stats = []
                        for i in invs:
                            stats.append({
                                "Gözetmen": f"Personel {i}", 
                                "Toplam Mesai (dk)": solver.Value(total_mins[i]), 
                                "Büyük Sınıf (dk)": solver.Value(big_mins[i]), 
                                "Sabah Görevi": solver.Value(morn_cnt[i]), 
                                "Akşam Görevi": solver.Value(eve_cnt[i]), 
                                "Kritik Toplam": solver.Value(critical_sum[i])
                            })
                        st.table(pd.DataFrame(stats))
                    
                    with t3:
                        st.markdown("### 🧠 Gelişmiş Optimizasyon Metodolojisi")
                        st.write("""
                        Bu sistem, Google tarafından geliştirilen **OR-Tools** kütüphanesinin en güçlü çözücüsü olan **CP-SAT** algoritmasını kullanmaktadır.
                        """)
                        
                        st.info("#### ⚙️ Algoritmik Çalışma Prensibi")
                        st.markdown("""
                        **1. Kısıt Programlama (Constraint Programming):** Sistem, problemleri "olması gerekenler" yerine "olması imkansız olanlar" (constraints) üzerinden tanımlar. Örneğin; bir gözetmen aynı saatteki iki sınavda bulunamaz. Bu bir 'Sert Kısıt'tır.

                        **2. SAT-Based Search (Boolean Satisfiability):** Milyonlarca olası atama kombinasyonu arasından, Boolean mantığını kullanarak kurallara uymayanları saniyeler içinde eler. Bu, geleneksel deneme-yanılma yöntemlerinden milyonlarca kat daha hızlıdır.

                        **3. Min-Max Normalizasyonu:** Sistem sadece atama yapmaz, aynı zamanda 'Adalet Skoru'nu hesaplar. En çok çalışan ile en az çalışan arasındaki farkı (range) kapatmak için sürekli optimizasyon yapar.

                        **4. Akşam Mesaisi Kümelenmesi (16:00 Kuralı):** Sizin talebiniz doğrultusunda, saat 16:00 ve sonrasındaki sınavlar 'Akşam' olarak etiketlenir. Algoritma, akşam sınavına kalacak personeli seçerken "eğer zaten akşam sınavındaysa, diğer akşam sınavını da ona vererek zaman verimliliğini artır" (clustering) mantığını kullanır.
                        """)
                        
                        st.latex(r"Minimize: \sum (W_i \times \Delta_{fark}) - \sum (P_{küme})")
                else:
                    st.error("❌ Mevcut kısıtlar ve personel sayısı ile uygun bir çözüm bulunamadı! Lütfen personel sayısını artırmayı veya muafiyetleri esnetmeyi deneyin.")
