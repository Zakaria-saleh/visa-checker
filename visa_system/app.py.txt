from flask import Flask, render_template, request, send_file, jsonify
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
import time
import os
from datetime import datetime
import io

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

BASE_URL = 'https://visa.mofa.gov.sa/Enjaz/PrintApplication?ApplicationNo={}'

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
            if 'رقم الطلب' in col or 'application' in col.lower() or 'request' in col.lower():
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
        return send_file(filename, as_attachment=True)
    except:
        return jsonify({'error': 'الملف غير موجود'}), 404

@app.route('/stats')
def get_stats():
    return jsonify({
        'status': 'System is running',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)