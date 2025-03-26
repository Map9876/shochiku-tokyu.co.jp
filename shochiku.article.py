import os
import requests
from bs4 import BeautifulSoup
import json
from urllib.parse import urljoin, urlparse, unquote
import re
import time
import sys
import io

# 配置
DATA_FILE = 'data~shochiku.json'
BASE_URL = 'https://www.shochiku-tokyu.co.jp'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}

# 初始化数据文件
def init_data_file():
    if not os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump({'last_post': None, 'posts': []}, f, ensure_ascii=False, indent=4)

# 加载数据
def load_data():
    with open(DATA_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)

# 保存数据
def save_data(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# 获取文章列表
def get_latest_posts(page):
    url = f'{BASE_URL}/notice/?p={page}'
    try:
        response = requests.get(url, headers=HEADERS, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        posts = []
        for item in soup.select('.p_notice-container_list_item'):
            link = urljoin(BASE_URL, item.find('a')['href'])
            title = item.find('p', class_='m_information-item-box_title').text.strip()
            date = item.find('p', class_='m_information-item-box_wrap_date').text.strip()
            
            # 获取封面图
            image_tag = item.find('img', class_='lazyload')
            image_src = urljoin(BASE_URL, image_tag['src']) if image_tag else None
            
            posts.append({
                'link': link,
                'title': title,
                'date': date,
                'image': image_src,
                'downloaded': False  # 标记是否已下载
            })
        
        return posts
    except Exception as e:
        print(f"获取文章列表失败 (第{page}页): {str(e)}")
        return []

# 获取文章内容图片
def get_article_images(article_url):
    try:
        response = requests.get(article_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        main_content = soup.find('main')
        if not main_content:
            return []
        
        # 匹配图片URL（排除带参数的缩略图）
        image_pattern = re.compile(r'\.(jpg|jpeg|png|gif|webp)$', re.IGNORECASE)
        image_urls = set()
        
        for tag in main_content.find_all(['img', 'source']):
            # 处理src属性
            if tag.has_attr('src'):
                src = tag['src'].split('?')[0]
                if image_pattern.search(src):
                    image_urls.add(urljoin(BASE_URL, src))
            
            # 处理srcset属性
            if tag.has_attr('srcset'):
                for src in tag['srcset'].split(','):
                    src = src.strip().split()[0].split('?')[0]
                    if image_pattern.search(src):
                        image_urls.add(urljoin(BASE_URL, src))
        
        return sorted(image_urls)
    except Exception as e:
        print(f"获取文章内容失败: {article_url} - {str(e)}")
        return []

# 下载图片
def download_image(img_url, download_dir, article_title):
    try:
        # 创建文章专属目录
        safe_title = re.sub(r'[\\/*?:"<>|]', '', article_title)[:50]
        article_dir = os.path.join(download_dir, safe_title)
        os.makedirs(article_dir, exist_ok=True)
        
        # 获取文件名
        parsed = urlparse(unquote(img_url))
        filename = os.path.basename(parsed.path)
        if not filename:
            ext = re.search(r'\.(\w+)$', img_url)
            ext = ext.group(1) if ext else 'jpg'
            filename = f"image_{int(time.time())}.{ext}"
        
        filepath = os.path.join(article_dir, filename)
        
        # 下载图片
        img_response = requests.get(img_url, headers=HEADERS, stream=True, timeout=15)
        img_response.raise_for_status()
        
        with open(filepath, 'wb') as f:
            for chunk in img_response.iter_content(8192):
                f.write(chunk)
        
        return filepath
    except Exception as e:
        print(f"下载图片失败: {img_url} - {str(e)}")
        return None

# 主流程
def main():
    # 初始化
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    init_data_file()
    data = load_data()
    
    # 1. 获取新文章
    print("正在检查新文章...")
    new_posts = []
    page = 1
    
    while True:
        posts = get_latest_posts(page)
        if not posts:
            break
        
        # 检查是否遇到已抓取的文章
        if any(post['link'] == data['last_post'] for post in posts):
            break
        
        new_posts.extend(posts)
        page += 1
        time.sleep(1)  # 礼貌性延迟
    
    if not new_posts:
        print("没有发现新文章")
        return
    
    print(f"发现 {len(new_posts)} 篇新文章")
    
    # 2. 处理每篇文章
    download_dir = 'downloaded_images'
    os.makedirs(download_dir, exist_ok=True)
    
    for post in new_posts:
        print(f"\n处理文章: {post['title']}")
        
        # 获取文章内容图片
        image_urls = get_article_images(post['link'])
        if not image_urls:
            print(" - 未找到内容图片")
            continue
        
        print(f" - 找到 {len(image_urls)} 张内容图片")
        
        # 下载图片
        downloaded = []
        for img_url in image_urls:
            saved_path = download_image(img_url, download_dir, post['title'])
            if saved_path:
                downloaded.append(saved_path)
                time.sleep(0.5)  # 下载延迟
        
        # 更新文章数据
        post['content_images'] = image_urls
        post['downloaded_images'] = downloaded
        post['downloaded'] = bool(downloaded)
    
    # 3. 更新数据文件
    if new_posts:
        data['last_post'] = new_posts[0]['link']
        data['posts'].extend(new_posts)
        save_data(data)
        print(f"\n已保存 {len(new_posts)} 篇新文章数据到 {DATA_FILE}")

if __name__ == '__main__':
    main()
