import math
import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium
import urllib.parse
import requests
from streamlit_geolocation import streamlit_geolocation

# 設定網頁標題與圖標
st.set_page_config(page_title="桃憩時光 - 桃園智慧咖啡廳搜尋", page_icon="☕", layout="wide")

# --- 效能優化：使用 Cache 快取資料庫 ---
@st.cache_data
def load_data():
    try:
        return pd.read_csv("cafe.csv")
    except FileNotFoundError:
        st.error("找不到 cafe.csv 檔案！請確認檔案與 app.py 在同一個資料夾。")
        return pd.DataFrame()

# 1. 哈維辛公式：計算兩個經緯度之間的直線距離
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0  
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# 2. 反向地理編碼：將經緯度轉回文字地址
@st.cache_data
def reverse_geocode(lat, lng):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lng}&format=json"
        headers = {'User-Agent': 'TaoCafeFinder/1.0 (student_project)'}
        response = requests.get(url, headers=headers, timeout=5)
        data = response.json()
        if data and "display_name" in data:
            address_elements = data.get("address", {})
            city = address_elements.get("city", address_elements.get("town", address_elements.get("county", "")))
            suburb = address_elements.get("suburb", address_elements.get("village", ""))
            road = address_elements.get("road", "")
            house_number = address_elements.get("house_number", "")
            formatted_address = f"{city}{suburb}{road}{house_number}"
            if formatted_address:
                return formatted_address
            return data["display_name"].split(',')[0]
    except Exception:
        pass
    return "您的當前位置"

# 3. 核心搜尋函式 (統一使用強大的本地資料庫)
def search_cafes(user_lat, user_lng, selected_tags, keyword="", max_distance_km=1.0):
    df = load_data()
    if df.empty:
        return df

    # 計算距離並過濾交通圈內的店家
    df["distance"] = df.apply(lambda row: haversine(user_lat, user_lng, row["lat"], row["lng"]), axis=1)
    filtered_df = df[df["distance"] <= max_distance_km].copy()

    # 處理標籤過濾 (如果 CSV 裡面沒有該標籤欄位，會自動忽略以防報錯)
    for tag in selected_tags:
        if tag in filtered_df.columns:
            filtered_df = filtered_df[filtered_df[tag] == 1]

    # 處理關鍵字過濾
    if keyword.strip():
        filtered_df = filtered_df[filtered_df["name"].str.contains(keyword, na=False, case=False)]
    
    return filtered_df

# ─── Streamlit 前端網頁介面設計 ───
st.title("☕ 桃憩時光 (Tao-Café Finder)")
st.subheader("專屬您的桃園咖啡廳交通圈導航")

st.write("### 📍 位置權限與起點設定")
location_consent = st.radio(
    "為了精準計算您與咖啡廳的距離，請選擇定位方式：",
    ("✅ 同意授權使用我目前的真實 GPS 定位", "❌ 不同意位置追蹤，我想自行輸入起點 / 手動設定起點"),
    horizontal=True
)

# 預設起點設為中壢火車站
my_lat, my_lng = 24.9537, 121.2256
current_loc_title = "中壢火車站 (預設起點)"

if location_consent == "✅ 同意授權使用我目前的真實 GPS 定位":
    st.info("👇 請點擊下方按鈕，讓瀏覽器確認這是您本人的操作")
    
    st.markdown(
        """
        <style>
        button[title="Get Location"] {
            transform: scale(2.0); 
            transform-origin: left center; 
            margin-top: 15px; margin-bottom: 15px; margin-left: 10px;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    gps_location = streamlit_geolocation()
    
    if gps_location and gps_location.get('latitude') is not None:
        my_lat = gps_location['latitude']
        my_lng = gps_location['longitude']
        real_address = reverse_geocode(my_lat, my_lng)
        st.success(f"🎯 GPS 定位成功！系統偵測到您目前位於：【{real_address}】")
        current_loc_title = f"您的位置 ({real_address})"
    else:
        st.warning("⚠️ 等待定位中... 如果無法定位，系統將以預設起點載入地圖。")

# ─── 側邊欄與搜尋控制 ───
st.sidebar.header("🔍 搜尋與篩選條件")

user_keyword = st.sidebar.text_input("請輸入店名關鍵字：", placeholder="例如：星巴克...")
transport_mode = st.sidebar.selectbox("🚗 請選擇代步工具：", ("🚶 步行", "🛵 機車", "🚗 汽車"))

if transport_mode == "🚶 步行":
    speed_per_minute, max_time_value, default_time_value = 0.07, 30, 15
    time_label, icon_name = "預計最大步行時間 (分鐘)", "user"       
elif transport_mode == "🛵 機車":
    speed_per_minute, max_time_value, default_time_value = 0.50, 60, 15
    time_label, icon_name = "預計最大騎車時間 (分鐘)", "motorcycle" 
else:
    speed_per_minute, max_time_value, default_time_value = 0.66, 90, 15
    time_label, icon_name = "預計最大開車時間 (分鐘)", "car"        

travel_minutes = st.sidebar.slider(time_label, min_value=5, max_value=max_time_value, value=default_time_value, step=5)
max_dist = travel_minutes * speed_per_minute

st.sidebar.write("📌 空間與氛圍標籤（可複選）：")
tag_dict = {
    "pudding": st.sidebar.checkbox("🍮 布丁好吃"),
    "basque": st.sidebar.checkbox("🍰 巴斯克好吃"),
    "midnight": st.sidebar.checkbox("🌙 主打深夜"),
    "study": st.sidebar.checkbox("💻 適合讀書"),
    "chat": st.sidebar.checkbox("💬 適合聊天"),
    "photo": st.sidebar.checkbox("📷 適合拍照"),
}
active_tags = [key for key, value in tag_dict.items() if value]

# 執行統一搜尋
results = search_cafes(my_lat, my_lng, active_tags, keyword=user_keyword, max_distance_km=max_dist)

# ─── 地圖與搜尋結果渲染 ───
st.write("---")
st.write("### 📍 地圖與搜尋結果")
action_verb = "步行" if "步行" in transport_mode else ("騎車" if "機車" in transport_mode else "開車")

current_zoom = 16 if "步行" in transport_mode else 14
mymap = folium.Map(location=[my_lat, my_lng], zoom_start=current_zoom)

# 標記起點
folium.Marker(
    location=[my_lat, my_lng],
    popup=f"<b>🎯 起點：{current_loc_title}</b>",
    icon=folium.Icon(color="red", icon=icon_name, prefix="fa"),
).add_to(mymap)

# 標記咖啡廳
if not results.empty:
    st.success(f"🎉 在您的 {travel_minutes} 分鐘 {action_verb}圈內，幫您找到 {len(results)} 間咖啡廳：")
    for _, row in results.iterrows():
        t_time = round(row["distance"] / speed_per_minute)
        t_time = 1 if t_time < 1 else t_time
        
        # 處理如果爬蟲抓下來的資料沒有特定欄位時的防呆機制
        address_display = row.get("address", "無地址資料")
        
        popup_text = f"<b>{row['name']}</b><br>距離：{row['distance']:.2f} km<br>{action_verb}約：{t_time} 分鐘"
        
        folium.Marker(
            location=[row["lat"], row["lng"]],
            popup=popup_text,
            tooltip=row['name'],
            icon=folium.Icon(color="blue", icon="coffee", prefix="fa"),
        ).add_to(mymap)
else:
    st.warning(f"💡 提示：在您的交通時間與篩選條件內，暫時沒有搜尋到咖啡廳。")

# 渲染地圖
st_folium(mymap, width=850, height=500, key="cafe_map")

# 顯示詳細資料列表
if not results.empty:
    st.write("#### 📝 店家詳細資訊清單：")
    
    # 動態挑選要顯示的欄位 (避免爬蟲抓的資料缺漏導致報錯)
    cols_to_show = ["name", "distance"]
    if "address" in results.columns: cols_to_show.insert(1, "address")
        
    display_df = results[cols_to_show].copy()
    
    # 重新命名欄位為中文
    rename_dict = {"name": "店家名稱", "address": "完整地址", "distance": "距離 (km)"}
    display_df.rename(columns=rename_dict, inplace=True)
    
    st.dataframe(display_df.sort_values(by="距離 (km)"), use_container_width=True)
