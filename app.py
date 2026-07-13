from flask import Flask, render_template, request, send_file, jsonify, session, redirect, url_for
from flask_cors import CORS
from functools import wraps
import pandas as pd
import requests
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
MEDICAL_URL = 'https://visa.mofa.gov.sa/visaperson/checkmedicalcert'

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

# ===== الدالة النهائية البسيطة =====
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
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=30)
        response.encoding = 'utf-8'
        
        if response.status_code == 200:
            html_text = response.text
            
            # استخراج البيانات الأساسية
            name_match = re.search(r'الاسم\s*</label>.*?<div[^>]*>(.*?)</div>', html_text, re.DOTALL)
            if name_match:
                result['applicant_name'] = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
            
            passport_match = re.search(r'رقم الجواز\s*</label>.*?<div[^>]*>(\d+)</div>', html_text, re.DOTALL)
            if passport_match:
                result['passport_number'] = passport_match.group(1).strip()
            
            type_match = re.search(r'نوع التأشيرة\s*</label>.*?<div[^>]*>(.*?)</div>', html_text, re.DOTALL)
            if type_match:
                result['visa_type'] = re.sub(r'<[^>]+>', '', type_match.group(1)).strip()
            
            date_match = re.search(r'تاريخ (?:الطلب|الإصدار)[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})', html_text)
            if date_match:
                result['issue_date'] = date_match.group(1).strip()
            
            # 🔥 القاعدة الجديدة: إذا تم استخراج البيانات الأساسية = مؤشر 🔥
            if result['applicant_name'] and result['passport_number'] and result['visa_type']:
                result['status'] = 'مؤشر'
            
            return result
        else:
            result['error'] = f'خطأ ({response.status_code})'
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
            is_issued = 'تم إصدار' in html or 'Issued' in html
            return {'has_certificate': is_issued, 'status': "تم الإصدار" if is_issued else "لم يتم الإصدار"}
        return {'has_certificate': False, 'status': f'خطأ ({response.status_code})'}
    except Exception as e:
        return {'has_certificate': False, 'status': 'خطأ'}

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
        
        col_name = None
        for col in df.columns:
            if 'رقم الطلب' in str(col) or 'application' in str(col).lower():
                col_name = col
                break
        
        if not col_name:
            return jsonify({'error': 'لم يتم العثور على عمود "رقم الطلب"'}), 400

        required_cols = ['الاسم', 'رقم الجواز', 'نوع التأشيرة', 'حالة التأشيرة', 'تاريخ الإصدار']
        for col in required_cols:
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
            time.sleep(2.5)

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
