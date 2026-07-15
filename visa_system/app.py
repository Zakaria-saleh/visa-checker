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

# إعداد النظام
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='visa_system/templates', static_folder='visa_system/static')
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.secret_key = 'visa-system-secret-key-2026-xyz'

VALID_USERNAME = 'زكريا السعدي'
VALID_PASSWORD_HASH = hashlib.sha256('773983986'.encode()).hexdigest()

BASE_URL = 'https://visa.mofa.gov.sa/Enjaz/PrintApplication?ApplicationNo={}'

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

# ===== الدالة الدقيقة جداً =====
def check_visa_status(app_number):
    """
    القاعدة:
    1. التأكد أن الصفحة هي صفحة بيانات تأشيرة وليست خطأ أو تسجيل دخول (بالبحث عن 'تاريخ الطلب').
    2. البحث عن وسم <label> يحتوي على 'رقم المستند'.
       - إذا وُجد: فهي مؤشرة.
       - إذا لم يُوجد: فهي غير مؤشرة.
    """
    url = BASE_URL.format(app_number)
    session_obj = requests.Session()
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ar-SA,ar;q=0.9,en;q=0.8',
        }
        response = session_obj.get(url, headers=headers, timeout=30)
        response.encoding = 'utf-8'

        if response.status_code != 200:
            return 'غير مؤشر'

        soup = BeautifulSoup(response.text, 'html.parser')

        # إزالة السكريبتات والستايلات لمنع الخداع
        for tag in soup(['script', 'style', 'noscript', 'meta']):
            tag.extract()

        # التحقق 1: هل الصفحة تحتوي على "تاريخ الطلب"؟ (للتأكد أنها صفحة تأشيرة صحيحة)
        date_label = soup.find('label', string=re.compile(r'تاريخ الطلب'))
        if not date_label:
            # إذا لم نجد تاريخ الطلب، فالصفحة خاطئة أو غير مكتملة
            return 'غير مؤشر'

        # التحقق 2: هل يوجد وسم label مكتوب فيه "رقم المستند"؟
        # هذا وسم HTML لا يوجد إلا في التأشيرات المؤشرة
        doc_label = soup.find('label', string=re.compile(r'رقم المستند'))
        
        if doc_label:
            logger.info(f"✅ الطلب {app_number}: مؤشر (تم العثور على رقم المستند)")
            return 'مؤشر'
        else:
            logger.info(f"❌ الطلب {app_number}: غير مؤشر (لم يتم العثور على رقم المستند)")
            return 'غير مؤشر'

    except Exception as e:
        logger.error(f"خطأ في فحص {app_number}: {str(e)}")
        return 'غير مؤشر'
    finally:
        session_obj.close()

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
        # قراءة الملف الأصلي
        df = pd.read_excel(file)

        # البحث عن عمود رقم الطلب
        col_name = None
        for col in df.columns:
            if 'رقم الطلب' in str(col) or 'application' in str(col).lower():
                col_name = col
                break

        if not col_name:
            return jsonify({'error': 'لم يتم العثور على عمود "رقم الطلب"'}), 400

        # إضافة عمود الحالة فقط
        if 'حالة التأشيرة' not in df.columns:
            df['حالة التأشيرة'] = ''

        total = len(df)
        success_count = 0
        error_count = 0

        for index, row in df.iterrows():
            app_no = str(row[col_name]).strip()
            status = check_visa_status(app_no)

            df.at[index, 'حالة التأشيرة'] = status

            if status == 'مؤشر':
                success_count += 1
            else:
                error_count += 1

            # تأخير لتجنب الحظر
            time.sleep(2.5)

        filename = f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='النتائج')
        output.seek(0)

        path = os.path.join(tempfile.gettempdir(), filename)
        with open(path, 'wb') as f:
            f.write(output.getvalue())
        gc.collect()

        return jsonify({
            'success': True,
            'total': total,
            'success_count': success_count,
            'error_count': error_count,
            'filename': filename
        }), 200

    except Exception as e:
        logger.error(f"خطأ في المعالجة: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    path = os.path.join(tempfile.gettempdir(), filename)
    if os.path.exists(path):
        return send_file(path, as_attachment=True)
    return jsonify({'error': 'الملف غير موجود'}), 404

@app.route('/stats')
@login_required
def get_stats():
    return jsonify({'status': 'running', 'user': session.get('username', '')})

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
