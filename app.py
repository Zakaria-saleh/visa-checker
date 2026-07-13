from flask import Flask, render_template, request, send_file, jsonify
from flask_cors import CORS
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import time
import os
from datetime import datetime
import io
import tempfile

app = Flask(__name__, template_folder='visa_system/templates', static_folder='visa_system/static')
CORS(app)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

BASE_URL = 'https://visa.mofa.gov.sa/Enjaz/PrintApplication?ApplicationNo={}'
MEDICAL_URL = 'https://visa.mofa.gov.sa/visaperson/checkmedicalresult'


def check_visa_status(app_number):
    url = BASE_URL.format(app_number)
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            html_text = response.text

            has_visa = False
            if 'تاريخ الإصدار' in html_text or 'Issue Date' in html_text:
                has_visa = True

            if has_visa:
                issue_date = "غير متوفر"
                visa_type = "غير محدد"

                date_pattern = re.compile(r'تاريخ الإصدار[:\s]+(\d{2}[/\-]\d{2}[/\-]\d{4})', re.I)
                match = date_pattern.search(html_text)
                if match:
                    issue_date = match.group(1)

                type_pattern = re.compile(r'نوع التأشيرة[:\s]+([^\n<]+)', re.I)
                type_match = type_pattern.search(html_text)
                if type_match:
                    visa_type = type_match.group(1).strip()

                return 'مؤشر', issue_date, visa_type
            else:
                return 'غير مؤشر', '', ''
        else:
            return f'خطأ ({response.status_code})', '', ''

    except Exception as e:
        return f'خطأ: {str(e)[:50]}', '', ''


def check_medical_certificate(app_number, passport_number):
    """التحقق من الشهادة الصحية برقم الطلب ورقم الجواز"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Referer': MEDICAL_URL
        }
        
        # البيانات المطلوبة
        data = {
            'ApplicationNo': app_number,
            'PassportNo': passport_number,
            'CaptchaCode': ''  # قد نحتاج captcha
        }
        
        response = requests.post(MEDICAL_URL, data=data, headers=headers, timeout=15)
        
        if response.status_code == 200:
            html_text = response.text
            soup = BeautifulSoup(html_text, 'html.parser')
            
            # البحث عن نتائج الشهادة الصحية
            has_certificate = False
            status = "غير متوفر"
            message = ""
            
            # البحث عن نصوص تدل على وجود شهادة
            if 'الشهادة الصحية' in html_text or 'Medical Certificate' in html_text:
                if 'تم إصدار' in html_text or 'Issued' in html_text:
                    has_certificate = True
                    status = "تم الإصدار"
                elif 'لم يتم' in html_text or 'Not Issued' in html_text:
                    status = "لم يتم الإصدار"
                else:
                    status = "موجود"
                    has_certificate = True
            
            # استخراج معلومات إضافية
            details = {}
            
            # البحث عن تاريخ الإصدار
            date_pattern = re.compile(r'تاريخ الإصدار[:\s]+(\d{2}[/\-]\d{2}[/\-]\d{4})', re.I)
            date_match = date_pattern.search(html_text)
            if date_match:
                details['issue_date'] = date_match.group(1)
            
            # البحث عن الحالة الصحية
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


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/process', methods=['POST'])
def process_file():
    if 'file' not in request.files:
        return jsonify({'error': 'لم يتم رفع ملف'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'لم يتم اختيار ملف'}), 400

    if not file.filename.endswith(('.xlsx', '.xls')):
        return jsonify({'error': 'الرجاء رفع ملف Excel فقط'}), 400

    try:
        df = pd.read_excel(file)

        col_name = None
        for col in df.columns:
            if 'رقم الطلب' in str(col) or 'application' in str(col).lower() or 'request' in str(col).lower():
                col_name = col
                break

        if not col_name:
            return jsonify({'error': 'لم يتم العثور على عمود "رقم الطلب"'}), 400

        df['حالة التأشيرة'] = ''
        df['تاريخ الإصدار'] = ''
        df['نوع التأشيرة'] = ''

        total = len(df)
        processed = 0
        success_count = 0
        error_count = 0

        for index, row in df.iterrows():
            app_no = str(row[col_name]).strip()
            status, date, visa_type = check_visa_status(app_no)

            df.at[index, 'حالة التأشيرة'] = status
            df.at[index, 'تاريخ الإصدار'] = date
            df.at[index, 'نوع التأشيرة'] = visa_type

            processed += 1
            if 'مؤشر' in status:
                success_count += 1
            else:
                error_count += 1

            time.sleep(2)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_filename = f'results_{timestamp}.xlsx'

        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='النتائج')
        output.seek(0)

        tmpdir = tempfile.gettempdir()
        output_path = os.path.join(tmpdir, output_filename)
        with open(output_path, 'wb') as f:
            f.write(output.getvalue())

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
def download_file(filename):
    try:
        tmpdir = tempfile.gettempdir()
        path = os.path.join(tmpdir, filename)
        return send_file(path, as_attachment=True)
    except Exception:
        return jsonify({'error': 'الملف غير موجود'}), 404


@app.route('/stats')
def get_stats():
    return jsonify({
        'status': 'System is running',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })


# ===== الميزة الجديدة: التحقق من الشهادة الصحية =====
@app.route('/check-medical', methods=['POST'])
def check_medical():
    """التحقق من الشهادة الصحية"""
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
        
        # التحقق من الشهادة الصحية
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
def process_medical_file():
    """معالجة ملف Excel للتحقق من الشهادات الصحية"""
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
        app_col = None
        for col in df.columns:
            if 'رقم الطلب' in str(col) or 'application' in str(col).lower():
                app_col = col
                break

        # البحث عن عمود رقم الجواز
        passport_col = None
        for col in df.columns:
            if 'رقم الجواز' in str(col) or 'passport' in str(col).lower():
                passport_col = col
                break

        if not app_col:
            return jsonify({'error': 'لم يتم العثور على عمود "رقم الطلب"'}), 400

        if not passport_col:
            return jsonify({'error': 'لم يتم العثور على عمود "رقم الجواز"'}), 400

        # إضافة أعمدة النتائج
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

            time.sleep(2)

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
