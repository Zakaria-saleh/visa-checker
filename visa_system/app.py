from flask import Flask, render_template, request, send_file, jsonify, session, redirect, url_for
from flask_cors import CORS
from functools import wraps
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import time
import os
from datetime import datetime
import io
import tempfile
import hashlib
import gc
import logging

# إعداد السجلات لتظهر في Console Render
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='visa_system/templates', static_folder='visa_system/static')
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.secret_key = 'visa-system-secret-key-2026-xyz'

VALID_USERNAME = 'زكريا السعدي'
VALID_PASSWORD_HASH = hashlib.sha256('773983986'.encode()).hexdigest()

BASE_URL = 'https://visa.mofa.gov.sa/Enjaz/PrintApplication?ApplicationNo={}'

# ===== هيدر متصفح حقيقي لتجنب الحظر =====
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'ar-SA,ar;q=0.9,en;q=0.8',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == VALID_USERNAME and hashlib.sha256(password.encode()).hexdigest() == VALID_PASSWORD_HASH:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('index'))
        return render_template('login.html', error='اسم المستخدم أو كلمة السر غير صحيحة')
    if 'logged_in' in session:
        return redirect(url_for('index'))
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ===== دالة الفحص الدقيقة مع إدارة الجلسة =====
def check_visa_status(app_number):
    """
    1. ينشئ جلسة جديدة ويحمل الصفحة الرئيسية لأخذ الكوكيز.
    2. يطلب صفحة الطباعة.
    3. يتحقق إن كان الرد صفحة تسجيل دخول (حظر/آي بي).
    4. يبحث عن <label>رقم المستند</label> بدقة.
    """
    req_session = requests.Session()
    try:
        # خطوة 1: تحميل الصفحة الرئيسية لتأسيس الجلسة والكوكيز
        req_session.get('https://visa.mofa.gov.sa/', headers=HEADERS, timeout=10, allow_redirects=True)
        
        # خطوة 2: طلب صفحة التأشيرة
        response = req_session.get(BASE_URL.format(app_number), headers=HEADERS, timeout=15, allow_redirects=True)
        response.encoding = 'utf-8'
        
        html_text = response.text
        
        #  كشف صفحة تسجيل الدخول (دليل على حظر الآي بي أو فقدان الجلسة)
        if 'UserName' in html_text and 'كلمة المرور' in html_text:
            logger.warning(f"⚠️ الطلب {app_number}: تم التوجيه لصفحة تسجيل الدخول (آي بي محظور أو جلسة منتهية)")
            return 'خطأ جلسة'

        # 🔍 البحث الدقيق عن وسم الرقم المستند
        soup = BeautifulSoup(html_text, 'html.parser')
        # إزالة السكريبتات لمنع التشويش
        for tag in soup(['script', 'style', 'noscript']):
            tag.extract()
            
        # البحث عن label يحتوي النص
        doc_label = soup.find('label', string=lambda t: t and 'رقم المستند' in t)
        
        if doc_label:
            logger.info(f"✅ الطلب {app_number}: مؤشر")
            return 'مؤشر'
        else:
            logger.info(f" الطلب {app_number}: غير مؤشر")
            return 'غير مؤشر'
            
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ خطأ اتصال في {app_number}: {e}")
        return 'خطأ اتصال'
    finally:
        req_session.close()

@app.route('/')
@login_required
def index():
    return render_template('index.html', username=session.get('username', ''))

@app.route('/process', methods=['POST'])
@login_required
def process_file():
    if 'file' not in request.files:
        return jsonify({'error': 'لم يتم رفع ملف'}), 400
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'الرجاء رفع ملف Excel فقط'}), 400

    try:
        df = pd.read_excel(file)
        col_name = next((c for c in df.columns if 'رقم الطلب' in str(c) or 'application' in str(c).lower()), None)
        if not col_name:
            return jsonify({'error': 'عمود رقم الطلب غير موجود'}), 400

        if 'حالة التأشيرة' not in df.columns:
            df['حالة التأشيرة'] = ''

        total = len(df)
        success = error = 0

        for idx, row in df.iterrows():
            app_no = str(row[col_name]).strip()
            logger.info(f"🔄 فحص {idx+1}/{total}: {app_no}")
            
            status = check_visa_status(app_no)
            df.at[idx, 'حالة التأشيرة'] = status
            
            if status == 'مؤشر': success += 1
            else: error += 1
            
            time.sleep(2.0) # تأخير آمن

        filename = f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        out = io.BytesIO()
        with pd.ExcelWriter(out, engine='openpyxl') as w:
            df.to_excel(w, index=False, sheet_name='النتائج')
        out.seek(0)
        
        path = os.path.join(tempfile.gettempdir(), filename)
        with open(path, 'wb') as f: f.write(out.getvalue())
        gc.collect()

        return jsonify({'success': True, 'total': total, 'success_count': success, 'error_count': error, 'filename': filename}), 200
    except Exception as e:
        logger.error(f"خطأ فادح: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    path = os.path.join(tempfile.gettempdir(), filename)
    return send_file(path, as_attachment=True) if os.path.exists(path) else (jsonify({'error': 'غير موجود'}), 404)

@app.route('/stats')
@login_required
def get_stats():
    return jsonify({'status': 'ready', 'user': session.get('username', '')})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
