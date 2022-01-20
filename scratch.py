def pretty_print_data():
    print('2020 Salary Totals')
    for row in salaries:
        text, df, titles = row
        pay = titles_in_category(df, titles)['Gross Pay']
        if not titles:
            titles = '(All)'
        data = sum_count(pay)
        print(f'Salary {text} [Titles: {titles}] ({data[1]} people): ${data[0]:,}')

def sum_count(df):
    return (df.sum(), df.count())

# Reload the title code map
# TITLE_CODE_MAP = pd.read_csv(TITLE_CODE_TYPE_SHEET)

# sum_count(all_berkeley_salaries[all_berkeley_salaries['Title'].isin(EXEC_ADMIN_TITLES)]['Gross Pay'])

