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
        
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        if username == VALID_USERNAME and password_hash == VALID_PASSWORD_HASH:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='اسم المستخدم أو كلمة السر غير صحيحة')
    
    if 'logged_in' in session:
        return redirect(url_for('index'))
    
    return render_template('login.html', error=None)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ===== دالة استخراج بيانات التأشيرة (القاعدة الدقيقة) =====
def extract_visa_data(app_number):
    """
    القاعدة الدقيقة 100%:
    - إذا تواجد النص "رقم المستند" في الصفحة = مؤشر ✅
    - إذا لم يتواجد = غير مؤشر ❌
    """
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
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ar-SA,ar;q=0.9,en;q=0.8'
        }
        response = requests.get(url, headers=headers, timeout=20)

        if response.status_code == 200:
            html_text = response.text
            
            # 🔍 القاعدة الأساسية: البحث عن "رقم المستند"
            # نبحث عن النص سواء كان داخل label أو أي مكان في الصفحة
            has_document_number = 'رقم المستند' in html_text or 'Document Number' in html_text
            
            if has_document_number:
                result['status'] = 'مؤشر'
                
                # استخراج تاريخ الإصدار/الطلب
                date_patterns = [
                    r'تاريخ الطلب[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})',
                    r'تاريخ الإصدار[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})',
                    r'Request Date[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})',
                    r'Issue Date[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})'
                ]
                for pattern in date_patterns:
                    match = re.search(pattern, html_text, re.I)
                    if match:
                        result['issue_date'] = match.group(1).strip()
                        break
                
                # استخراج نوع التأشيرة
                type_patterns = [
                    r'نوع التأشيرة[:\s]*([^\n<]+?)(?:\s*(?:عدد|اسم|الممثلة|<))',
                    r'Visa Type[:\s]*([^\n<]+?)(?:\s*(?:Entry|Name|<))'
                ]
                for pattern in type_patterns:
                    match = re.search(pattern, html_text, re.I)
                    if match:
                        visa_type = match.group(1).strip()
                        visa_type = re.sub(r'[\s\n]+', ' ', visa_type).strip()
                        if visa_type and len(visa_type) > 1:
                            result['visa_type'] = visa_type
                            break
                
                # استخراج اسم الشخص
                name_patterns = [
                    r'الاسم[:\s]*([^\n<]+?)(?:\s*(?:Name|<))',
                    r'اسم الشخص.*?الطالبية[:\s]*([^\n<]+)',
                    r'Applicant Name[:\s]*([^\n<]+)'
                ]
                for pattern in name_patterns:
                    match = re.search(pattern, html_text, re.I)
                    if match:
                        result['applicant_name'] = match.group(1).strip()
                        result['applicant_name'] = re.sub(r'[\s\n]+', ' ', result['applicant_name']).strip()
                        if len(result['applicant_name']) > 2:
                            break
                
                # استخراج رقم الجواز
                passport_patterns = [
                    r'رقم الجواز[:\s]*(\d{7,12})',
                    r'Passport Number[:\s]*(\d{7,12})'
                ]
                for pattern in passport_patterns:
                    match = re.search(pattern, html_text, re.I)
                    if match:
                        result['passport_number'] = match.group(1).strip()
                        break
            else:
                # غير مؤشر
                result['status'] = 'غير مؤشر'
            
            return result
        else:
            result['error'] = f'خطأ ({response.status_code})'
            return result

    except Exception as e:
        result['error'] = f'خطأ: {str(e)[:100]}'
        return result


# ===== دالة التحقق من الشهادة الصحية =====
def check_medical_certificate(app_number, passport_number):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Referer': MEDICAL_URL
        }
        
        data = {
            'ApplicationNo': app_number,
            'PassportNo': passport_number,
            'CaptchaCode': ''
        }
        
        response = requests.post(MEDICAL_URL, data=data, headers=headers, timeout=15)
        
        if response.status_code == 200:
            html_text = response.text
            has_certificate = 'تم إصدار' in html_text or 'Issued' in html_text or 'موجود' in html_text
            
            return {
                'has_certificate': has_certificate,
                'status': "تم الإصدار" if has_certificate else "لم يتم الإصدار",
                'details': {},
                'message': ''
            }
        else:
            return {'has_certificate': False, 'status': f'خطأ ({response.status_code})', 'details': {}, 'message': ''}
            
    except Exception as e:
        return {'has_certificate': False, 'status': 'خطأ', 'details': {}, 'message': f'خطأ: {str(e)[:50]}'}


# ===== المسارات الرئيسية =====
@app.route('/')
@login_required
def index():
    return render_template('index.html', username=session.get('username', ''))


@app.route('/process', methods=['POST'])
@login_required
def process_file():
    """معالجة ملف Excel - الحفاظ على الأعمدة الأصلية وإضافة الأعمدة الجديدة فقط"""
    if 'file' not in request.files:
        return jsonify({'error': 'لم يتم رفع ملف'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'لم يتم اختيار ملف'}), 400

    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'الرجاء رفع ملف Excel فقط'}), 400

    try:
        # قراءة الملف الأصلي كما هو
        df = pd.read_excel(file)

        # البحث عن عمود رقم الطلب
        col_name = None
        for col in df.columns:
            if 'رقم الطلب' in str(col) or 'application' in str(col).lower() or 'request' in str(col).lower():
                col_name = col
                break

        if not col_name:
            return jsonify({'error': 'لم يتم العثور على عمود "رقم الطلب"'}), 400

        # إضافة الأعمدة الجديدة إلى الملف الأصلي (بدون حذف أي بيانات موجودة)
        df['الاسم'] = ''
        df['رقم الجواز'] = ''
        df['نوع التأشيرة'] = ''
        df['حالة التأشيرة'] = ''
        df['تاريخ الإصدار'] = ''

        total = len(df)
        success_count = 0
        error_count = 0

        for index, row in df.iterrows():
            app_no = str(row[col_name]).strip()
            
            # استخراج البيانات
            visa_data = extract_visa_data(app_no)
            
            # تعبئة الأعمدة الجديدة في الصف الحالي
            df.at[index, 'الاسم'] = visa_data['applicant_name']
            df.at[index, 'رقم الجواز'] = visa_data['passport_number']
            df.at[index, 'نوع التأشيرة'] = visa_data['visa_type']
            df.at[index, 'حالة التأشيرة'] = visa_data['status']
            df.at[index, 'تاريخ الإصدار'] = visa_data['issue_date']

            if visa_data['status'] == 'مؤشر':
                success_count += 1
            else:
                error_count += 1

            time.sleep(0.5)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_filename = f'results_{timestamp}.xlsx'

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # حفظ الملف مع جميع الأعمدة الأصلية + الأعمدة الجديدة المضافة
            df.to_excel(writer, index=False, sheet_name='النتائج')
        output.seek(0)

        tmpdir = tempfile.gettempdir()
        output_path = os.path.join(tmpdir, output_filename)
        with open(output_path, 'wb') as f:
            f.write(output.getvalue())

        gc.collect()

        return jsonify({
            'success': True,
            'total': total,
            'success_count': success_count,
            'error_count': error_count,
            'filename': output_filename
        }), 200

    except Exception as e:
        return jsonify({'error': f'حدث خطأ: {str(e)}'}), 500


@app.route('/download/<filename>')
@login_required
def download_file(filename):
    try:
        tmpdir = tempfile.gettempdir()
        path = os.path.join(tmpdir, filename)
        return send_file(path, as_attachment=True)
    except Exception:
        return jsonify({'error': 'الملف غير موجود'}), 404


@app.route('/stats')
@login_required
def get_stats():
    return jsonify({
        'status': 'System is running',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'user': session.get('username', '')
    })


@app.route('/check-medical', methods=['POST'])
@login_required
def check_medical():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'لم يتم استلام البيانات'}), 400
        
        app_number = data.get('application_number', '').strip()
        passport_number = data.get('passport_number', '').strip()
        
        if not app_number or not passport_number:
            return jsonify({'error': 'رقم الطلب ورقم الجواز مطلوبان'}), 400
        
        result = check_medical_certificate(app_number, passport_number)
        return jsonify({'success': True, 'result': result}), 200
    except Exception as e:
        return jsonify({'error': f'حدث خطأ: {str(e)}'}), 500


@app.route('/process-medical', methods=['POST'])
@login_required
def process_medical_file():
    if 'file' not in request.files:
        return jsonify({'error': 'لم يتم رفع ملف'}), 400

    file = request.files['file']
    if file.filename == '' or not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'الرجاء رفع ملف Excel صحيح'}), 400

    try:
        df = pd.read_excel(file)

        app_col = next((col for col in df.columns if 'رقم الطلب' in str(col) or 'application' in str(col).lower()), None)
        passport_col = next((col for col in df.columns if 'رقم الجواز' in str(col) or 'passport' in str(col).lower()), None)

        if not app_col or not passport_col:
            return jsonify({'error': 'يجب وجود أعمدة "رقم الطلب" و "رقم الجواز"'}), 400

        df['حالة الشهادة الصحية'] = ''
        df['تفاصيل'] = ''

        total = len(df)
        success_count = 0

        for index, row in df.iterrows():
            result = check_medical_certificate(str(row[app_col]).strip(), str(row[passport_col]).strip())
            df.at[index, 'حالة الشهادة الصحية'] = result['status']
            if result['has_certificate']:
                success_count += 1
            time.sleep(0.5)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_filename = f'medical_results_{timestamp}.xlsx'

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='النتائج')
        output.seek(0)

        tmpdir = tempfile.gettempdir()
        output_path = os.path.join(tmpdir, output_filename)
        with open(output_path, 'wb') as f:
            f.write(output.getvalue())

        gc.collect()

        return jsonify({
            'success': True, 'total': total, 'success_count': success_count,
            'error_count': total - success_count, 'filename': output_filename
        }), 200

    except Exception as e:
        return jsonify({'error': f'حدث خطأ: {str(e)}'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ('1', 'true', 'yes')
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
