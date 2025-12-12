import streamlit as st
import openrouteservice
from openrouteservice import convert
import google.generativeai as genai
import speech_recognition as sr
from gtts import gTTS
from streamlit_mic_recorder import mic_recorder
import io
import tempfile
import os

# --- CẤU HÌNH ---
st.set_page_config(page_title="Bus Assistant (Free Version)", page_icon="🚌", layout="wide")

# --- QUẢN LÝ SECRETS ---
try:
    # Key bản đồ miễn phí (OpenRouteService)
    ORS_API_KEY = st.secrets.get("ORS_API_KEY", "") 
    # Key AI (Vẫn dùng Gemini vì nó free)
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
except:
    st.error("⚠️ Chưa cấu hình Secrets. Vui lòng thêm ORS_API_KEY và GEMINI_API_KEY.")
    st.stop()

if not ORS_API_KEY:
    # Nếu chạy local mà chưa có secrets, nhập tạm vào đây để test
    ORS_API_KEY = st.text_input("Nhập OpenRouteService Key (Miễn phí):", type="password")

# --- HÀM TÌM ĐỊA ĐIỂM & ĐƯỜNG ĐI (Dùng OpenRouteService) ---
def get_coordinates(address, client):
    """Đổi địa chỉ thành tọa độ (Geocoding)"""
    try:
        geocode = client.pelias_search(text=address)
        if geocode['features']:
            # Lấy tọa độ điểm đầu tiên tìm thấy [long, lat]
            coords = geocode['features'][0]['geometry']['coordinates']
            label = geocode['features'][0]['properties']['label']
            return coords, label
        return None, None
    except Exception as e:
        return None, str(e)

def get_route_ors(start_addr, end_addr, client):
    """Tìm đường đi bộ/xe (Sửa lỗi return type)"""
    # 1. Tìm tọa độ điểm đi/đến
    start_coords, start_label = get_coordinates(start_addr, client)
    end_coords, end_label = get_coordinates(end_addr, client)
    
    # [FIX LỖI TẠI ĐÂY]: Trả về None trước, Error sau
    if not start_coords or not end_coords:
        missing = start_addr if not start_coords else end_addr
        return None, f"Không tìm thấy địa điểm: {missing}. Vui lòng nhập cụ thể hơn (Ví dụ: thêm 'TPHCM')."

    try:
        # 2. Tìm đường
        route = client.directions(
            coordinates=[start_coords, end_coords],
            profile='foot-walking', 
            format='geojson',
            language='vi'
        )
        
        # 3. Trích xuất thông tin
        summary = route['features'][0]['properties']['segments'][0]
        distance_km = round(summary['distance'] / 1000, 2)
        duration_min = round(summary['duration'] / 60)
        
        steps = summary['steps']
        step_text = ""
        for step in steps:
            step_text += f"- {step['instruction']} ({step['distance']}m)\n"

        return {
            "start": start_label,
            "end": end_label,
            "distance": f"{distance_km} km",
            "duration": f"{duration_min} phút đi bộ",
            "steps": step_text,
            "raw_steps": steps
        }, None

    except Exception as e:
        return None, f"Lỗi tìm đường: {str(e)}"
        
# --- CÁC HÀM ÂM THANH (GIỮ NGUYÊN) ---
def text_to_speech(text):
    try:
        tts = gTTS(text=text, lang='vi')
        fp = io.BytesIO()
        tts.write_to_fp(fp)
        return fp
    except: return None

def process_audio(audio_bytes):
    r = sr.Recognizer()
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp.write(audio_bytes)
            name = tmp.name
        with sr.AudioFile(name) as src:
            audio = r.record(src)
            text = r.recognize_google(audio, language="vi-VN")
        os.remove(name)
        return text
    except: return None

# --- GIAO DIỆN ---
st.title("🚌 Trợ Lý Di Chuyển (Bản Miễn Phí)")
st.caption("Dữ liệu bản đồ từ OpenStreetMap & AI Gemini")

# Setup Client
if ORS_API_KEY:
    ors_client = openrouteservice.Client(key=ORS_API_KEY)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-flash-latest')

col1, col2 = st.columns([1, 1])

# CỘT 1: TÌM ĐƯỜNG
with col1:
    st.subheader("📍 Nhập lộ trình")
    start_in = st.text_input("Điểm đi", "Chợ Bến Thành")
    end_in = st.text_input("Điểm đến", "Dinh Độc Lập")
    
    if st.button("Tìm đường"):
        if ORS_API_KEY:
            with st.spinner("Đang tìm trên bản đồ mở..."):
                data, err = get_route_ors(start_in, end_in, ors_client)
                
                if err:
                    st.error(err)
                elif data:
                    st.success(f"Từ: {data['start']}\nĐến: {data['end']}")
                    st.metric("Khoảng cách", data['distance'], data['duration'])
                    
                    # Context cho AI
                    # MẸO: Vì ORS Free không có dữ liệu xe buýt tốt, ta nhờ AI "chém" dựa trên địa điểm
                    context = f"""
                    Người dùng muốn đi từ {data['start']} đến {data['end']}.
                    Khoảng cách thực tế: {data['distance']}. Thời gian đi bộ: {data['duration']}.
                    Chi tiết đường đi bộ: {data['steps']}
                    """
                    st.session_state['route_context'] = context
                    st.session_state['location_data'] = data
                    
                    with st.expander("Xem hướng dẫn đi bộ"):
                        st.text(data['steps'])
        else:
            st.warning("Vui lòng nhập API Key ORS.")

# CỘT 2: CHAT AI TƯ VẤN XE BUÝT
with col2:
    st.subheader("🤖 AI Tư Vấn Xe Buýt")
    
    # Hiển thị chat
    if "messages" not in st.session_state: st.session_state.messages = []
    for m in st.session_state.messages: st.chat_message(m["role"]).write(m["content"])

    # Input
    mic = mic_recorder(start_prompt="🎤", stop_prompt="⏹️", key='mic_btn')
    txt = st.chat_input("Hỏi về xe buýt tuyến này...")
    
    final_input = txt
    if mic and ('last_id' not in st.session_state or st.session_state.last_id != mic['id']):
        st.session_state.last_id = mic['id']
        t = process_audio(mic['audio']['bytes'])
        if t: final_input = t

    if final_input:
        st.session_state.messages.append({"role":"user", "content":final_input})
        st.chat_message("user").write(final_input)
        
        # PROMPT ĐẶC BIỆT ĐỂ BÙ ĐẮP THIẾU DỮ LIỆU GOOGLE MAPS
        ctx = st.session_state.get('route_context', '')
        prompt = f"""
        Bạn là trợ lý xe buýt thông minh tại Việt Nam.
        Hiện tại hệ thống bản đồ chỉ cung cấp được dữ liệu đi bộ và khoảng cách.
        
        Thông tin hiện có:
        {ctx}
        
        NHIỆM VỤ CỦA BẠN:
        1. Dựa vào kiến thức chung của bạn (đã được học từ internet), hãy ĐỀ XUẤT tuyến xe buýt phù hợp để đi giữa 2 địa điểm trên (Ví dụ ở TPHCM thì gợi ý xe số mấy, ở Hà Nội gợi ý xe nào).
        2. Nếu khoảng cách gần (< 1km), khuyên người dùng đi bộ.
        3. Trả lời câu hỏi: "{final_input}"
        4. Trả lời ngắn gọn, thân thiện.
        """
        
        try:
            res = model.generate_content(prompt).text
            st.session_state.messages.append({"role":"assistant", "content":res})
            st.chat_message("assistant").write(res)
            
            # Đọc to
            aud = text_to_speech(res)
            if aud: st.audio(aud, format='audio/mp3', start_time=0)
            
        except Exception as e:

            st.error(f"Lỗi AI: {e}")

