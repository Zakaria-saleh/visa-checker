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


# ===== دالة استخراج بيانات التأشيرة مع التحقق من عدد الباركودات =====
def extract_visa_data(app_number):
    """
    القاعدة الجديدة:
    - باركودين (يمين + يسار) = مؤشر ✅
    - باركود واحد فقط = غير مؤشر ❌
    """
    url = BASE_URL.format(app_number)
    
    result = {
        'status': 'غير مؤشر',
        'document_number': '',
        'issue_date': '',
        'visa_type': '',
        'applicant_name': '',
        'applicant_name_en': '',
        'passport_number': '',
        'passport_type': '',
        'entry_count': '',
        'representation': '',
        'request_date': '',
        'barcode_count': 0,
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
            soup = BeautifulSoup(html_text, 'html.parser')
            
            # ===== 1️ عدّ الباركودات =====
            barcode_count = 0
            barcode_images = []
            
            # البحث عن جميع الصور التي قد تكون باركود
            for img in soup.find_all('img'):
                src = img.get('src', '').lower()
                alt = img.get('alt', '').lower()
                class_name = ' '.join(img.get('class', [])).lower()
                id_name = img.get('id', '').lower()
                
                # مؤشرات الباركود
                barcode_indicators = [
                    'barcode', 'bar-code', 'bar_code',
                    'bcs', 'bcimg', 'imgbarcode',
                    'img-barcode', 'img_barcode',
                    'printapplication', 'enjaz'
                ]
                
                is_barcode = False
                for indicator in barcode_indicators:
                    if indicator in src or indicator in alt or indicator in class_name or indicator in id_name:
                        is_barcode = True
                        break
                
                # التحقق من حجم الصورة (الباركود عادة صغير وعريض)
                width = img.get('width', '')
                height = img.get('height', '')
                
                # إذا كانت الصورة عريضة (عرض > ارتفاع) فهي غالباً باركود
                if is_barcode:
                    barcode_count += 1
                    barcode_images.append({
                        'src': src,
                        'alt': alt,
                        'width': width,
                        'height': height
                    })
                elif width and height:
                    try:
                        w = int(width)
                        h = int(height)
                        # باركود عادة عرضه أكبر من ارتفاعه بـ 3 مرات على الأقل
                        if w > 100 and w > h * 2:
                            barcode_count += 1
                            barcode_images.append({
                                'src': src,
                                'alt': alt,
                                'width': width,
                                'height': height
                            })
                    except:
                        pass
            
            # البحث عن SVG للباركود
            for svg in soup.find_all('svg'):
                svg_class = ' '.join(svg.get('class', [])).lower()
                if 'barcode' in svg_class or 'bcs' in svg_class:
                    barcode_count += 1
            
            result['barcode_count'] = barcode_count
            
            # ===== 2️⃣ القرار: هل مؤشرة أم لا؟ =====
            # القاعدة: باركودين = مؤشر، باركود واحد = غير مؤشر
            is_issued = barcode_count >= 2
            
            if is_issued:
                result['status'] = 'مؤشر'
                
                # استخراج رقم المستند (الأسود)
                document_number = ''
                doc_patterns = [
                    r'رقم المستند[:\s]*(\d{7,12})',
                    r'Document Number[:\s]*(\d{7,12})',
                    r'Document No[:\s]*(\d{7,12})',
                    r'رقم المستند.*?(\d{7,12})'
                ]
                
                for pattern in doc_patterns:
                    match = re.search(pattern, html_text, re.I | re.S)
                    if match:
                        document_number = match.group(1).strip()
                        break
                
                result['document_number'] = document_number
                
                # استخراج تاريخ الإصدار/الطلب (الأصفر)
                issue_date = ''
                date_patterns = [
                    r'تاريخ الطلب[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})',
                    r'Request Date[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})',
                    r'تاريخ الطلب.*?(\d{2}[/\-]\d{2}[/\-]\d{4})'
                ]
                
                for pattern in date_patterns:
                    match = re.search(pattern, html_text, re.I | re.S)
                    if match:
                        issue_date = match.group(1).strip()
                        break
                
                if not issue_date:
                    issue_patterns = [
                        r'تاريخ الإصدار[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})',
                        r'Issue Date[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})'
                    ]
                    
                    for pattern in issue_patterns:
                        match = re.search(pattern, html_text, re.I | re.S)
                        if match:
                            issue_date = match.group(1).strip()
                            break
                
                result['issue_date'] = issue_date
                
                # استخراج نوع التأشيرة (الأحمر)
                visa_type = ''
                type_patterns = [
                    r'نوع التأشيرة[:\s]*([^\n<]+?)(?:\s*(?:عدد|اسم|الممثلة|<))',
                    r'Visa Type[:\s]*([^\n<]+?)(?:\s*(?:Entry|Name|<))',
                    r'نوع التأشيرة.*?(\w+)'
                ]
                
                for pattern in type_patterns:
                    match = re.search(pattern, html_text, re.I | re.S)
                    if match:
                        visa_type = match.group(1).strip()
                        visa_type = re.sub(r'[\s\n]+', ' ', visa_type).strip()
                        if visa_type and len(visa_type) > 1 and len(visa_type) < 50:
                            break
                
                result['visa_type'] = visa_type
                
                # استخراج اسم الشخص (عربي)
                applicant_name = ''
                name_patterns = [
                    r'الاسم[:\s]*([^\n<]+?)(?:\s*(?:Name|<))',
                    r'اسم الشخص.*?الطالبية[:\s]*([^\n<]+)',
                    r'Applicant Name[:\s]*([^\n<]+)'
                ]
                
                for pattern in name_patterns:
                    match = re.search(pattern, html_text, re.I | re.S)
                    if match:
                        applicant_name = match.group(1).strip()
                        applicant_name = re.sub(r'[\s\n]+', ' ', applicant_name).strip()
                        if applicant_name and len(applicant_name) > 2:
                            break
                
                result['applicant_name'] = applicant_name
                
                # استخراج اسم الشخص (إنجليزي)
                applicant_name_en = ''
                name_en_patterns = [
                    r'Name[:\s]*([A-Z\s]+?)(?:\s*(?:نوع|<|$))',
                    r'Name[:\s]*([A-Z][A-Z\s]+)'
                ]
                
                for pattern in name_en_patterns:
                    match = re.search(pattern, html_text)
                    if match:
                        applicant_name_en = match.group(1).strip()
                        if applicant_name_en and len(applicant_name_en) > 2:
                            break
                
                result['applicant_name_en'] = applicant_name_en
                
                # استخراج رقم الجواز
                passport_number = ''
                passport_patterns = [
                    r'رقم الجواز[:\s]*(\d{7,12})',
                    r'Passport Number[:\s]*(\d{7,12})',
                    r'رقم الجواز.*?(\d{7,12})'
                ]
                
                for pattern in passport_patterns:
                    match = re.search(pattern, html_text, re.I | re.S)
                    if match:
                        passport_number = match.group(1).strip()
                        break
                
                result['passport_number'] = passport_number
                
                # استخراج نوع الجواز
                passport_type = ''
                passport_type_patterns = [
                    r'نوع الجواز[:\s]*([^\n<]+)',
                    r'Passport Type[:\s]*([^\n<]+)'
                ]
                
                for pattern in passport_type_patterns:
                    match = re.search(pattern, html_text, re.I | re.S)
                    if match:
                        passport_type = match.group(1).strip()
                        break
                
                result['passport_type'] = passport_type
                
                # استخراج عدد مرات الدخول
                entry_count = ''
                entry_patterns = [
                    r'عدد مرات الدخول[:\s]*([^\n<]+)',
                    r'Number of Entries[:\s]*([^\n<]+)'
                ]
                
                for pattern in entry_patterns:
                    match = re.search(pattern, html_text, re.I | re.S)
                    if match:
                        entry_count = match.group(1).strip()
                        break
                
                result['entry_count'] = entry_count
                
                # استخراج الممثلة في
                representation = ''
                rep_patterns = [
                    r'الممثلة في[:\s]*([^\n<]+)',
                    r'Represented in[:\s]*([^\n<]+)'
                ]
                
                for pattern in rep_patterns:
                    match = re.search(pattern, html_text, re.I | re.S)
                    if match:
                        representation = match.group(1).strip()
                        break
                
                result['representation'] = representation
                
                # استخراج تاريخ الطلب
                request_date = ''
                req_date_patterns = [
                    r'تاريخ الطلب[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})',
                    r'Request Date[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{4})'
                ]
                
                for pattern in req_date_patterns:
                    match = re.search(pattern, html_text, re.I | re.S)
                    if match:
                        request_date = match.group(1).strip()
                        break
                
                result['request_date'] = request_date
                
            else:
                # غير مؤشر - باركود واحد أو لا يوجد
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
            
            has_certificate = False
            status = "غير متوفر"
            message = ""
            
            if 'الشهادة الصحية' in html_text or 'Medical Certificate' in html_text:
                if 'تم إصدار' in html_text or 'Issued' in html_text:
                    has_certificate = True
                    status = "تم الإصدار"
                elif 'لم يتم' in html_text or 'Not Issued' in html_text:
                    status = "لم يتم الإصدار"
                else:
                    status = "موجود"
                    has_certificate = True
            
            details = {}
            
            date_pattern = re.compile(r'تاريخ الإصدار[:\s]+(\d{2}[/\-]\d{2}[/\-]\d{4})', re.I)
            date_match = date_pattern.search(html_text)
            if date_match:
                details['issue_date'] = date_match.group(1)
            
            health_pattern = re.compile(r'الحالة[:\s]+([^\n<]+)', re.I)
            health_match = health_pattern.search(html_text)
            if health_match:
                details['health_status'] = health_match.group(1).strip()
            
            return {
                'has_certificate': has_certificate,
                'status': status,
                'details': details,
                'message': message
            }
        else:
            return {
                'has_certificate': False,
                'status': f'خطأ ({response.status_code})',
                'details': {},
                'message': 'فشل الاتصال بالخادم'
            }
            
    except Exception as e:
        return {
            'has_certificate': False,
            'status': 'خطأ',
            'details': {},
            'message': f'خطأ: {str(e)[:50]}'
        }


# ===== المسارات الرئيسية =====
@app.route('/')
@login_required
def index():
    return render_template('index.html', username=session.get('username', ''))


@app.route('/process', methods=['POST'])
@login_required
def process_file():
    """معالجة ملف Excel - استخراج البيانات بالصيغة المطلوبة"""
    if 'file' not in request.files:
        return jsonify({'error': 'لم يتم رفع ملف'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'لم يتم اختيار ملف'}), 400

    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'الرجاء رفع ملف Excel فقط'}), 400

    try:
        df = pd.read_excel(file)

        # البحث عن عمود رقم الطلب
        col_name = None
        for col in df.columns:
            if 'رقم الطلب' in str(col) or 'application' in str(col).lower() or 'request' in str(col).lower():
                col_name = col
                break

        if not col_name:
            return jsonify({'error': 'لم يتم العثور على عمود "رقم الطلب"'}), 400

        # قائمة لتخزين النتائج
        results = []

        total = len(df)
        success_count = 0
        error_count = 0

        for index, row in df.iterrows():
            app_no = str(row[col_name]).strip()
            
            # استخراج البيانات
            visa_data = extract_visa_data(app_no)
            
            # إضافة النتيجة
            result_row = {
                'الاسم': visa_data['applicant_name'],
                'رقم الجواز': visa_data['passport_number'],
                'رقم الطلب': app_no,
                'نوع التأشيرة': visa_data['visa_type'],
                'حالة التأشيرة': visa_data['status'],
                'تاريخ الإصدار': visa_data['issue_date']
            }
            
            results.append(result_row)

            if visa_data['status'] == 'مؤشر':
                success_count += 1
            else:
                error_count += 1

            time.sleep(0.5)

        # إنشاء DataFrame من النتائج بالترتيب المطلوب
        output_df = pd.DataFrame(results)
        
        # ترتيب الأعمدة كما في الصورة
        column_order = ['الاسم', 'رقم الجواز', 'رقم الطلب', 'نوع التأشيرة', 'حالة التأشيرة', 'تاريخ الإصدار']
        output_df = output_df[column_order]

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_filename = f'results_{timestamp}.xlsx'

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            output_df.to_excel(writer, index=False, sheet_name='النتائج')
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
        
        if not app_number:
            return jsonify({'error': 'رقم الطلب مطلوب'}), 400
        
        if not passport_number:
            return jsonify({'error': 'رقم الجواز مطلوب'}), 400
        
        result = check_medical_certificate(app_number, passport_number)
        
        return jsonify({
            'success': True,
            'application_number': app_number,
            'passport_number': passport_number,
            'result': result
        }), 200
        
    except Exception as e:
        return jsonify({'error': f'حدث خطأ: {str(e)}'}), 500


@app.route('/process-medical', methods=['POST'])
@login_required
def process_medical_file():
    if 'file' not in request.files:
        return jsonify({'error': 'لم يتم رفع ملف'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'لم يتم اختيار ملف'}), 400

    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'الرجاء رفع ملف Excel فقط'}), 400

    try:
        df = pd.read_excel(file)

        app_col = None
        for col in df.columns:
            if 'رقم الطلب' in str(col) or 'application' in str(col).lower():
                app_col = col
                break

        passport_col = None
        for col in df.columns:
            if 'رقم الجواز' in str(col) or 'passport' in str(col).lower():
                passport_col = col
                break

        if not app_col:
            return jsonify({'error': 'لم يتم العثور على عمود "رقم الطلب"'}), 400

        if not passport_col:
            return jsonify({'error': 'لم يتم العثور على عمود "رقم الجواز"'}), 400

        df['حالة الشهادة الصحية'] = ''
        df['تفاصيل'] = ''

        total = len(df)
        success_count = 0
        error_count = 0

        for index, row in df.iterrows():
            app_no = str(row[app_col]).strip()
            passport_no = str(row[passport_col]).strip()
            
            result = check_medical_certificate(app_no, passport_no)
            
            df.at[index, 'حالة الشهادة الصحية'] = result['status']
            
            details_text = []
            if 'issue_date' in result['details']:
                details_text.append(f"تاريخ الإصدار: {result['details']['issue_date']}")
            if 'health_status' in result['details']:
                details_text.append(f"الحالة: {result['details']['health_status']}")
            
            df.at[index, 'تفاصيل'] = ' | '.join(details_text) if details_text else result['message']

            if result['has_certificate']:
                success_count += 1
            else:
                error_count += 1

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
            'success': True,
            'total': total,
            'success_count': success_count,
            'error_count': error_count,
            'filename': output_filename
        }), 200

    except Exception as e:
        return jsonify({'error': f'حدث خطأ: {str(e)}'}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() in ('1', 'true', 'yes')
    app.run(debug=debug_mode, host='0.0.0.0', port=port)
