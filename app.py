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

# --- 效能優化：使用 Cache 快取資料庫，避免每次操作都重新讀取 CSV ---
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

# 2. 正向地理編碼：將文字地址轉成經緯度
@st.cache_data
def geocode_address(address):
    default_lat, default_lng = 24.9537, 121.2256
    if not address.strip() or address == "中壢火車站":
        return default_lat, default_lng
    try:
        url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(address)}&format=json&limit=1"
        headers = {'User-Agent': 'TaoCafeFinder/1.0 (student_project)'}
        response = requests.get(url, headers=headers, timeout=5)
        data = response.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception:
        pass
    return default_lat, default_lng

# 3. 反向地理編碼：將經緯度轉回文字地址
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

# 4. 本地資料庫智慧搜尋函式
def search_cafes(user_lat, user_lng, selected_tags, keyword="", max_distance_km=1.0):
    df = load_data()
    if df.empty:
        return df

    df["distance"] = df.apply(lambda row: haversine(user_lat, user_lng, row["lat"], row["lng"]), axis=1)
    filtered_df = df[df["distance"] <= max_distance_km].copy()

    for tag in selected_tags:
        filtered_df = filtered_df[filtered_df[tag] == 1]

    if keyword.strip():
        filtered_df = filtered_df[filtered_df["name"].str.contains(keyword, na=False, case=False)]
    return filtered_df

# 5. 新增：Google Places API (New) 即時連線搜尋函式
@st.cache_data(ttl=1800)  # 快取 30 分鐘，兼顧即時性與額度節省
def search_google_cafes(user_lat, user_lng, max_distance_km, keyword=""):
    # 從安全保險箱安全讀取金鑰
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
    else:
        st.error("🔑 找不到 GOOGLE_API_KEY！請確認 `.streamlit/secrets.toml` 檔案已正確建立並寫入金鑰。")
        return pd.DataFrame()

    # 如果有輸入關鍵字，使用 Text Search 文本搜尋；沒輸入則用 Nearby Search 鄰近搜尋
    if keyword.strip():
        url = "https://places.googleapis.com/v1/places:searchText"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.regularOpeningHours.openNow,places.location"
        }
        payload = {
            "textQuery": f"桃園 {keyword} 咖啡廳",
            "languageCode": "zh-TW"
        }
    else:
        url = "https://places.googleapis.com/v1/places:searchNearby"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.displayName,places.formattedAddress,places.regularOpeningHours.openNow,places.location"
        }
        payload = {
            "includedTypes": ["coffee_shop"],
            "maxResultCount": 20, # 限制回傳數量，避免地圖太雜亂
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": user_lat, "longitude": user_lng},
                    "radius": min(int(max_distance_km * 1000), 50000) # 轉為公尺，上限 50 公里
                }
            },
            "languageCode": "zh-TW"
        }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=5)
        response.raise_for_status()
        data = response.json()

        cafe_list = []
        if "places" in data:
            for place in data["places"]:
                lat = place.get("location", {}).get("latitude")
                lng = place.get("location", {}).get("longitude")
                
                if lat and lng:
                    # 使用原本的哈維辛公式精準計算使用者與店家的直線距離
                    dist = haversine(user_lat, user_lng, lat, lng)
                    
                    # 如果是 Text Search，需要手動過濾超出代步工具圈的店家
                    if keyword.strip() and dist > max_distance_km:
                        continue
                    
                    # 轉換營業狀態為好看的標籤
                    open_now = place.get("regularOpeningHours", {}).get("openNow")
                    if open_now is True:
                        status = "🟢 營業中"
                    elif open_now is False:
                        status = "🔴 休息中"
                    else:
                        status = "⏳ 營業時間請見 Google"

                    cafe_list.append({
                        "name": place.get("displayName", {}).get("text", "未知店名"),
                        "address": place.get("formattedAddress", "無地址"),
                        "open_hours": status,
                        "lat": lat,
                        "lng": lng,
                        "distance": dist
                    })
        return pd.DataFrame(cafe_list)
    except Exception as e:
        st.error(f"📡 Google API 連線失敗: {e}")
        return pd.DataFrame()


# ─── Streamlit 前端網頁介面設計 ───
st.title("☕ 桃憩時光 (Tao-Café Finder)")
st.subheader("桃園專屬智慧標籤與交通圈咖啡廳導航系統")

st.write("### 📍 位置權限與起點設定")
location_consent = st.radio(
    "【隱私授權詢問】為了計算您與咖啡廳的距離，本系統需要設定您的出發位置：",
    ("✅ 同意授權使用我目前的真實 GPS 定位", "❌ 不同意位置追蹤，我想自行輸入起點 / 手動設定起點"),
    horizontal=True
)

my_lat, my_lng = 24.9537, 121.2256
current_loc_title = "中壢火車站"

if location_consent == "✅ 同意授權使用我目前的真實 GPS 定位":
    st.info("👇 請點擊下方按鈕，讓瀏覽器確認這是您本人的操作")
    
    st.markdown(
        """
        <style>
        button[title="Get Location"] {
            transform: scale(2.0); 
            transform-origin: left center; 
            margin-top: 15px;
            margin-bottom: 15px;
            margin-left: 10px;
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
        st.warning("""
        ⚠️ **【系統提示：等待定位中】**
        請點擊上方按鈕，並在手機彈出的視窗選擇「允許」。
        如果依然無法定位，請勾選上方的 **「❌ 不同意位置追蹤」** 進行手動輸入。
        """)
        st.info("ℹ️ 目前地圖暫時先幫您以預設起點【中壢火車站】載入。")

# ─── 側邊欄與搜尋控制 ───
st.sidebar.header("🔍 搜尋與篩選條件")

# 新增：搜尋模式切換器
search_mode = st.sidebar.radio(
    "🧱 請選擇資料來源模式：",
    ("📂 本地資料庫 (精選氛圍標籤)", "🌐 Google Maps Live (即時最新店家)")
)

user_keyword = st.sidebar.text_input("請輸入咖啡廳店名關鍵字：", placeholder="例如：妮咖啡...")
transport_mode = st.sidebar.selectbox("🚗 請選擇您的代步工具：", ("🚶 步行", "🛵 機車", "🚗 汽車"))

if transport_mode == "🚶 步行":
    speed_per_minute = 0.07  
    max_time_value = 30
    default_time_value = 15
    time_label = "預計最大步行時間 (分鐘)"
    icon_name = "user"       
elif transport_mode == "🛵 機車":
    speed_per_minute = 0.50  
    max_time_value = 60
    default_time_value = 15  
    time_label = "預計最大騎車時間 (分鐘)"
    icon_name = "motorcycle" 
else:
    speed_per_minute = 0.66  
    max_time_value = 90
    default_time_value = 15  
    time_label = "預計最大開車時間 (分鐘)"
    icon_name = "car"        

travel_minutes = st.sidebar.slider(time_label, min_value=5, max_value=max_time_value, value=default_time_value, step=5)
max_dist = travel_minutes * speed_per_minute

# 根據搜尋模式，切換側邊欄 UI 展示
if search_mode == "📂 本地資料庫 (精選氛圍標籤)":
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
    
    # 執行本地 CSV 篩選
    results = search_cafes(my_lat, my_lng, active_tags, keyword=user_keyword, max_distance_km=max_dist)
else:
    st.sidebar.info("ℹ️ Google Maps Live 模式直接對接官方實時資料庫，暫不支援自訂氛圍標籤篩選。")
    
    # 執行 Google API 即時線上抓取
    with st.spinner("正在即時連線 Google Maps 獲取最新情報..."):
        results = search_google_cafes(my_lat, my_lng, max_distance_km=max_dist, keyword=user_keyword)


# ─── 地圖與搜尋結果渲染 ───
st.write(f"### 📍 地圖與搜尋結果 ({search_mode})")
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
        popup_text = f"<b>{row['name']}</b><br>距離：{row['distance']:.2f} km<br>{action_verb}約：{t_time} 分鐘<br>狀態：{row['open_hours']}"
        
        folium.Marker(
            location=[row["lat"], row["lng"]],
            popup=popup_text,
            icon=folium.Icon(color="blue", icon="coffee", prefix="fa"),
        ).add_to(mymap)
else:
    st.warning(f"💡 提示：目前定位在【{current_loc_title}】，在您選擇的交通時間與篩選條件內，暫時沒有搜尋到咖啡廳。")

# 渲染地圖
st_folium(mymap, width=850, height=500, key="cafe_map")

# 顯示詳細資料列表
if not results.empty:
    st.write("#### 📝 店家詳細資訊清單：")
    # 將欄位名稱重命名，讓前端表格更精緻好看
    display_df = results[["name", "address", "open_hours", "distance"]].copy()
    display_df.columns = ["店家名稱", "完整地址", "營業狀態/時間", "距離 (km)"]
    st.dataframe(display_df.sort_values(by="距離 (km)"), use_container_width=True)
