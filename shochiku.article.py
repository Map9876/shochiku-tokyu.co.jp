import os
import requests
from bs4 import BeautifulSoup
import json
from urllib.parse import urljoin, urlparse, unquote
import re
import time
import sys
import io
import concurrent.futures
from functools import partial

# 配置
DATA_FILE = 'data~shochiku.json'
BASE_URL = 'https://www.shochiku-tokyu.co.jp'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
MAX_WORKERS = 10  # 并发工作线程数
REQUEST_TIMEOUT = 15  # 请求超时时间

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

# 获取文章列表 - 并行获取多页
def get_latest_posts_parallel(max_pages=10):
    def fetch_page(page):
        url = f'{BASE_URL}/notice/?p={page}'
        try:
            response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
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
                    'downloaded': False
                })
            
            return posts
        except Exception as e:
            print(f"获取文章列表失败 (第{page}页): {str(e)}")
            return []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(fetch_page, page) for page in range(1, max_pages+1)]
        all_posts = []
        for future in concurrent.futures.as_completed(futures):
            posts = future.result()
            if posts:
                all_posts.extend(posts)
    
    return all_posts

# 获取文章内容图片
def get_article_images(article_url):
    try:
        response = requests.get(article_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        main_content = soup.find('main')
        if not main_content:
            return []
        
        # 使用集合避免重复图片
        image_urls = set()
        image_pattern = re.compile(r'\.(jpg|jpeg|png|gif|webp)(?:\?|$)', re.IGNORECASE)
        
        # 一次性查找所有图片标签
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

# 下载单个图片
def download_single_image(img_url, download_dir, article_title):
    try:
        # 创建文章专属目录
        safe_title = re.sub(r'[\\/*?:"<>|]', '', article_title)[:50]
        article_dir = os.path.join(download_dir, safe_title)
        os.makedirs(article_dir, exist_ok=True)
        
        # 获取文件名
        parsed = urlparse(unquote(img_url))
        filename = os.path.basename(parsed.path)
        if not filename:
            ext = re.search(r'\.(\w+)(?:\?|$)', img_url, re.IGNORECASE)
            ext = ext.group(1) if ext else 'jpg'
            filename = f"image_{int(time.time())}.{ext}"
        
        filepath = os.path.join(article_dir, filename)
        
        # 下载图片
        img_response = requests.get(img_url, headers=HEADERS, stream=True, timeout=REQUEST_TIMEOUT)
        img_response.raise_for_status()
        
        with open(filepath, 'wb') as f:
            for chunk in img_response.iter_content(8192):
                f.write(chunk)
        
        return filepath
    except Exception as e:
        print(f"下载图片失败: {img_url} - {str(e)}")
        return None

# 处理单篇文章
def process_article(post, download_dir):
    print(f"处理文章: {post['title']}")
    
    # 获取文章内容图片
    image_urls = get_article_images(post['link'])
    if not image_urls:
        print(f" - {post['title']} 未找到内容图片")
        return None
    
    print(f" - {post['title']} 找到 {len(image_urls)} 张内容图片")
    
    # 并行下载图片
    download_func = partial(download_single_image, download_dir=download_dir, article_title=post['title'])
    downloaded = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(download_func, img_url) for img_url in image_urls]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                downloaded.append(result)
    
    # 更新文章数据
    post['content_images'] = image_urls
    post['downloaded_images'] = downloaded
    post['downloaded'] = bool(downloaded)
    
    return post

# 主流程
def main():
    # 初始化
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    init_data_file()
    data = load_data()
    
    # 1. 并行获取新文章
    print("正在并行检查新文章...")
    all_posts = get_latest_posts_parallel(max_pages=10)
    
    # 筛选未处理的新文章
    existing_links = {post['link'] for post in data['posts']}
    new_posts = [post for post in all_posts if post['link'] not in existing_links]
    
    if not new_posts:
        print("没有发现新文章")
        return
    
    print(f"发现 {len(new_posts)} 篇新文章")
    
    # 2. 并行处理每篇文章
    download_dir = 'downloaded_images'
    os.makedirs(download_dir, exist_ok=True)
    
    processed_posts = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_article, post, download_dir) for post in new_posts]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                processed_posts.append(result)
    
    # 3. 更新数据文件
    if processed_posts:
        data['last_post'] = processed_posts[0]['link']
        data['posts'].extend(processed_posts)
        save_data(data)
        print(f"\n已保存 {len(processed_posts)} 篇新文章数据到 {DATA_FILE}")

if __name__ == '__main__':
    start_time = time.time()
    main()
    print(f"总执行时间: {time.time() - start_time:.2f}秒")
