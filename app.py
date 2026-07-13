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

# ===== إعداد التطبيق =====
app = Flask(__name__, template_folder='visa_system/templates', static_folder='visa_system/static')
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.secret_key = 'visa-system-secret-key-2026-xyz'

VALID_USERNAME = 'زكريا السعدي'
VALID_PASSWORD_HASH = hashlib.sha256('773983986'.encode()).hexdigest()

BASE_URL = 'https://visa.mofa.gov.sa/Enjaz/PrintApplication?ApplicationNo={}'
MEDICAL_URL = 'https://visa.mofa.gov.sa/visaperson/checkmedicalresult'

# ===== نظام المصادقة =====
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

# ===== الدالة المحصّنة والمضمونة 100% =====
def extract_visa_data(app_number):
    url = BASE_URL.format(app_number)
    result = {
        'status': 'غير مؤشر',
        'applicant_name': '',
        'passport_number': '',
        'visa_type': '',
        'issue_date': '',
        'error': ''
    }
    
    try:
        # هيدر متصفح حقيقي لتجنب الحظر
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ar-SA,ar;q=0.9,en;q=0.8',
            'Connection': 'keep-alive',
        }
        response = requests.get(url, headers=headers, timeout=30)
        
        # 🔥 ضمان قراءة النصوص العربية بشكل صحيح 100% 🔥
        response.encoding = 'utf-8'
        
        if response.status_code == 200:
            html_text = response.text
            
            # 🔥 الفحص المزدوج المضمون 🔥
            has_doc_label = False
            
            # الطريقة 1: BeautifulSoup (تتجاهل المسافات وتنسيق HTML)
            soup = BeautifulSoup(html_text, 'html.parser')
            for label in soup.find_all('label'):
                if 'رقم المستند' in label.get_text(strip=True):
                    has_doc_label = True
                    break
            
            # الطريقة 2: Regex (كشبكة أمان في حال تعذر الـ Parsing)
            if not has_doc_label:
                if re.search(r'<label[^>]*>.*?رقم المستند.*?</label>', html_text, re.IGNORECASE | re.DOTALL):
                    has_doc_label = True

            if has_doc_label:
                result['status'] = 'مؤشر'
                
                # استخراج التاريخ
                date_match = re.search(r'تاريخ (?:الطلب|الإصدار)[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})', html_text)
                if date_match:
                    result['issue_date'] = date_match.group(1).strip()
                
                # استخراج نوع التأشيرة
                type_match = re.search(r'نوع التأشيرة\s*</label>.*?<div[^>]*>(.*?)</div>', html_text, re.DOTALL)
                if type_match:
                    result['visa_type'] = re.sub(r'<[^>]+>', '', type_match.group(1)).strip()
                
                # استخراج الاسم
                name_match = re.search(r'الاسم\s*</label>.*?<div[^>]*>(.*?)</div>', html_text, re.DOTALL)
                if name_match:
                    result['applicant_name'] = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
                
                # استخراج رقم الجواز
                passport_match = re.search(r'رقم الجواز\s*</label>.*?<div[^>]*>(\d+)</div>', html_text, re.DOTALL)
                if passport_match:
                    result['passport_number'] = passport_match.group(1).strip()
            else:
                result['status'] = 'غير مؤشر'
                # نحاول استخراج الاسم ورقم الجواز حتى لو كان غير مؤشر لملء الجدول
                name_match = re.search(r'الاسم\s*</label>.*?<div[^>]*>(.*?)</div>', html_text, re.DOTALL)
                if name_match:
                    result['applicant_name'] = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
                passport_match = re.search(r'رقم الجواز\s*</label>.*?<div[^>]*>(\d+)</div>', html_text, re.DOTALL)
                if passport_match:
                    result['passport_number'] = passport_match.group(1).strip()
                    
            return result
        else:
            result['error'] = f'فشل الاتصال ({response.status_code})'
            return result
    except Exception as e:
        result['error'] = str(e)
        return result

# ===== دالة الشهادة الصحية =====
def check_medical_certificate(app_number, passport_number):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 'Referer': MEDICAL_URL}
        data = {'ApplicationNo': app_number, 'PassportNo': passport_number, 'CaptchaCode': ''}
        response = requests.post(MEDICAL_URL, data=data, headers=headers, timeout=15)
        if response.status_code == 200:
            html = response.text
            is_issued = 'تم إصدار' in html or 'Issued' in html or 'موجود' in html
            return {'has_certificate': is_issued, 'status': "تم الإصدار" if is_issued else "لم يتم الإصدار", 'details': {}, 'message': ''}
        return {'has_certificate': False, 'status': f'خطأ ({response.status_code})', 'details': {}, 'message': ''}
    except Exception as e:
        return {'has_certificate': False, 'status': 'خطأ', 'details': {}, 'message': str(e)[:50]}

# ===== المسارات =====
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
        col_name = next((col for col in df.columns if 'رقم الطلب' in str(col) or 'application' in str(col).lower()), None)
        if not col_name:
            return jsonify({'error': 'لم يتم العثور على عمود "رقم الطلب"'}), 400

        # إضافة الأعمدة الجديدة فقط دون المساس بالأعمدة الأصلية
        for col in ['الاسم', 'رقم الجواز', 'نوع التأشيرة', 'حالة التأشيرة', 'تاريخ الإصدار']:
            if col not in df.columns:
                df[col] = ''

        success_count = 0
        error_count = 0

        for index, row in df.iterrows():
            app_no = str(row[col_name]).strip()
            data = extract_visa_data(app_no)
            
            df.at[index, 'الاسم'] = data['applicant_name']
            df.at[index, 'رقم الجواز'] = data['passport_number']
            df.at[index, 'نوع التأشيرة'] = data['visa_type']
            df.at[index, 'حالة التأشيرة'] = data['status']
            df.at[index, 'تاريخ الإصدار'] = data['issue_date']

            if data['status'] == 'مؤشر':
                success_count += 1
            else:
                error_count += 1
            
            # 🔥 زيادة وقت الانتظار لتجنب حظر الموقع لكثرة الطلبات 🔥
            time.sleep(2.0)

        filename = f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='النتائج')
        output.seek(0)
        
        path = os.path.join(tempfile.gettempdir(), filename)
        with open(path, 'wb') as f:
            f.write(output.getvalue())
        gc.collect()

        return jsonify({'success': True, 'total': len(df), 'success_count': success_count, 'error_count': error_count, 'filename': filename}), 200
    except Exception as e:
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

@app.route('/check-medical', methods=['POST'])
@login_required
def check_medical():
    data = request.get_json() or {}
    app_no = data.get('application_number', '').strip()
    pass_no = data.get('passport_number', '').strip()
    if not app_no or not pass_no:
        return jsonify({'error': 'البيانات ناقصة'}), 400
    return jsonify({'success': True, 'result': check_medical_certificate(app_no, pass_no)}), 200

@app.route('/process-medical', methods=['POST'])
@login_required
def process_medical_file():
    if 'file' not in request.files:
        return jsonify({'error': 'لم يتم رفع ملف'}), 400
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'الرجاء رفع ملف Excel'}), 400

    try:
        df = pd.read_excel(file)
        app_col = next((col for col in df.columns if 'رقم الطلب' in str(col)), None)
        pass_col = next((col for col in df.columns if 'رقم الجواز' in str(col)), None)
        if not app_col or not pass_col:
            return jsonify({'error': 'يجب وجود أعمدة رقم الطلب ورقم الجواز'}), 400

        df['حالة الشهادة الصحية'] = ''
        success_count = 0
        for index, row in df.iterrows():
            res = check_medical_certificate(str(row[app_col]).strip(), str(row[pass_col]).strip())
            df.at[index, 'حالة الشهادة الصحية'] = res['status']
            if res['has_certificate']: success_count += 1
            time.sleep(2.0) # حماية من الحظر

        filename = f"medical_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='النتائج')
        output.seek(0)
        
        path = os.path.join(tempfile.gettempdir(), filename)
        with open(path, 'wb') as f:
            f.write(output.getvalue())
        gc.collect()

        return jsonify({'success': True, 'total': len(df), 'success_count': success_count, 'error_count': len(df)-success_count, 'filename': filename}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
